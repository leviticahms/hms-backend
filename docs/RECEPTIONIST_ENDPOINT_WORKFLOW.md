# Receptionist Endpoint Workflow

This document maps the receptionist API surface to the code paths it uses today.

It also includes the closely related staff doctor schedule endpoints used by receptionists
for doctor availability management.

It answers, for each endpoint:

- which router handler receives the request
- which service/helper functions it calls
- which database/session path it uses
- which tables it reads or writes
- which project files are touched in the workflow

Line numbers are current for this branch and may drift as the code changes.

## Router entrypoints

- Main receptionist router:
  - `app/api/v1/routers/management/receptionist_management.py`
  - Prefix: `/receptionist`
- Receptionist directory router:
  - `app/api/v1/routers/management/receptionist_directory.py`
  - Mounted from `app/api/v1/api.py`
  - Receptionist-facing paths are available under `/receptionist`
- Related staff doctor schedule router:
  - `app/api/v1/routers/management/staff_doctor_schedules.py`
  - Prefix: `/staff/doctor-schedules`
  - Used by receptionist/nurse for doctor availability template management

## Shared wiring

Most receptionist endpoints branch through one of these dependency providers first:

- `app/api/v1/routers/management/receptionist_management.py:71`
  - `get_receptionist_tenant_db()`
  - Opens the hospital tenant DB session.
  - Used by patient list/search/documents.

- `app/api/v1/routers/management/receptionist_management.py:88`
  - `get_receptionist_tenant_clinical_service()`
  - Builds `ClinicalService(tenant_db, platform_db=platform_db, tenant_db=tenant_db)`.
  - Used by tenant-only OPD patient CRUD and patient profile fetch.

- `app/api/v1/routers/management/receptionist_management.py:110`
  - `get_receptionist_clinical_service()`
  - Builds `ClinicalService(platform_db, platform_db=platform_db, tenant_db=tenant_db)` when a tenant DB exists.
  - Current behavior note from code: appointments write on platform and read across platform + tenant.

Shared infra helpers:

- `app/database/tenant_context.py:22` -> `resolve_tenant_database_name_for_hospital()`
- `app/database/session.py:110` -> `get_tenant_session_factory()`
- `app/utils/hospital_id_resolve.py:22` -> `resolve_effective_hospital_id()`

Additional receptionist directory helpers:

- `app/api/v1/routers/management/receptionist_directory.py:76` -> `_hospital_id()`
- `app/api/v1/routers/management/receptionist_directory.py:166` -> `_doctor_query()`
- `app/api/v1/routers/management/receptionist_directory.py:445` -> `_department_query()`
- `app/api/v1/routers/management/receptionist_directory.py:702` -> `_appointments_for_date()`

## Database/storage pattern summary

From the current code:

- OPD patient registration/profile data is tenant-first.
- Receptionist document upload/list is tenant-first and also writes files under `uploads/`.
- Receptionist profile endpoints use the platform DB session directly.
- Appointment endpoints currently use the mixed service path:
  - reads may merge tenant + platform
  - writes are still performed through the platform-side `self.db`
  - scheduling now mirrors the patient profile into platform before inserting the appointment so the FK succeeds

## Endpoint workflows

### `GET /api/v1/receptionist/dashboard`

- Router handler:
  - `app/api/v1/routers/management/receptionist_management.py:275`
  - `get_receptionist_dashboard()`
- Main service:
  - `app/services/clinical_service.py:1946`
  - `ClinicalService.get_opd_dashboard()`
- Workflow:
  1. Validates receptionist access.
  2. Resolves hospital context.
  3. Reads today's appointments across `_opd_db_sessions()`.
  4. Counts today's patient registrations from tenant patient storage.
- Reads/writes:
  - Reads `appointments`
  - Reads `patient_profiles`
  - No writes
- Files touched:
  - `app/api/v1/routers/management/receptionist_management.py`
  - `app/services/clinical_service.py`

### `POST /api/v1/receptionist/patients/register`

- Router handler:
  - `app/api/v1/routers/management/receptionist_management.py:303`
  - `register_patient()`
- Main service:
  - `app/services/clinical_service.py:661`
  - `ClinicalService.register_opd_patient()`
- Additional helpers:
  - `app/services/email_service.py:192` -> `EmailService.is_smtp_configured()`
  - `app/services/clinical_service.py:110` -> `send_opd_portal_credentials_email_task()`
  - `app/services/patient_tenant_bridge.py:131` -> `mirror_patient_auth_to_platform()`
- Workflow:
  1. Router sends payload into tenant clinical service.
  2. Service validates hospital and uniqueness.
  3. Creates patient `User`.
  4. Assigns PATIENT role.
  5. Creates `PatientProfile`.
  6. Mirrors patient auth to platform for login.
  7. Router optionally queues credentials email.
- Reads/writes:
  - Tenant DB writes `users`, `roles`, `user_roles`, `patient_profiles`
  - Platform DB writes mirrored `users`, `roles`, `user_roles`
  - Platform DB reads `hospitals` to return `hospital_name`
- Files touched:
  - `app/api/v1/routers/management/receptionist_management.py`
  - `app/services/clinical_service.py`
  - `app/services/email_service.py`
  - `app/services/patient_tenant_bridge.py`
  - `app/services/auth_service.py`
  - `app/core/utils.py`

### `PATCH /api/v1/receptionist/patients/{patient_ref}`
### `PUT /api/v1/receptionist/patients/{patient_ref}`

- Router handler:
  - `app/api/v1/routers/management/receptionist_management.py:362`
  - `patch_opd_patient()`
- Main service:
  - `app/services/clinical_service.py:900`
  - `ClinicalService.patch_opd_patient()`
- Additional helpers:
  - `app/services/email_service.py:192` -> `EmailService.is_smtp_configured()`
  - `app/services/clinical_service.py:110` -> `send_opd_portal_credentials_email_task()`
  - `app/services/patient_tenant_bridge.py:131` -> `mirror_patient_auth_to_platform()`
  - `app/utils/receptionist_serializers.py:110` -> `build_receptionist_patient_full_payload()`
- Workflow:
  1. Router builds patch payload.
  2. Service loads patient by `patient_ref` inside the tenant path.
  3. Updates patient `User` and `PatientProfile`.
  4. Mirrors auth-side changes to platform if needed.
  5. Router optionally queues credentials email if password changed.
- Reads/writes:
  - Tenant DB reads/writes `users`, `patient_profiles`
  - Platform DB may write mirrored `users`, `roles`, `user_roles`
- Files touched:
  - `app/api/v1/routers/management/receptionist_management.py`
  - `app/services/clinical_service.py`
  - `app/services/email_service.py`
  - `app/services/patient_tenant_bridge.py`
  - `app/services/auth_service.py`
  - `app/utils/receptionist_serializers.py`

### `POST /api/v1/receptionist/appointments/schedule`

- Router handler:
  - `app/api/v1/routers/management/receptionist_management.py:417`
  - `schedule_appointment()`
- Main service:
  - `app/services/clinical_service.py:1226`
  - `ClinicalService.schedule_opd_appointment()`
- Important downstream helpers:
  - `app/services/clinical_service.py:1161` -> `_resolve_patient_for_scheduling()`
  - `app/services/appointment_service.py:117` -> `AppointmentService.get_available_time_slots_for_doctor_user()`
  - `app/services/patient_tenant_bridge.py:145` -> `mirror_opd_patient_to_platform()`
- Workflow:
  1. Resolves patient from `patient_ref` or exact `patient_name`.
  2. Resolves doctor and department.
  3. Validates requested time slot against doctor schedule.
  4. Ensures patient profile exists in platform before appointment insert.
  5. Creates appointment row and commits.
- Reads/writes:
  - Reads `patient_profiles`
  - Reads `users`
  - Reads `departments`
  - Reads `doctor_profiles`
  - Reads doctor schedule data via `AppointmentService`
  - Writes `appointments`
  - May write platform `users`, `roles`, `user_roles`, `patient_profiles` via patient mirroring
- Files touched:
  - `app/api/v1/routers/management/receptionist_management.py`
  - `app/services/clinical_service.py`
  - `app/services/appointment_service.py`
  - `app/services/patient_tenant_bridge.py`
  - `app/core/utils.py`
  - `app/utils/doctor_department_resolve.py`

### `GET /api/v1/receptionist/appointments/today`

- Router handler:
  - `app/api/v1/routers/management/receptionist_management.py:450`
  - `get_todays_appointments()`
- Main service:
  - `app/services/clinical_service.py:1428`
  - `ClinicalService.get_todays_opd_appointments()`
- Serializer:
  - `app/utils/receptionist_serializers.py:169` -> `serialize_opd_appointment_table_row()`
- Workflow:
  1. Builds filter payload from query params.
  2. Reads today's appointments for the hospital.
  3. Joins patient, doctor, and department details.
  4. Returns paginated table rows.
- Reads/writes:
  - Reads `appointments`
  - Reads related `patient_profiles`, `users`, `departments`
  - No writes
- Files touched:
  - `app/api/v1/routers/management/receptionist_management.py`
  - `app/services/clinical_service.py`
  - `app/utils/receptionist_serializers.py`

### `GET /api/v1/receptionist/appointments/{appointment_ref}`

- Router handler:
  - `app/api/v1/routers/management/receptionist_management.py:495`
  - `get_appointment_by_ref()`
- Main service:
  - `app/services/clinical_service.py:1568`
  - `ClinicalService.get_opd_appointment_by_ref()`
- Serializer:
  - `app/utils/receptionist_serializers.py:201` -> `serialize_opd_appointment_full()`
- Workflow:
  1. Loads appointment by `appointment_ref`.
  2. Ensures it belongs to the receptionist hospital context.
  3. Expands patient/doctor/department fields for detail view.
- Reads/writes:
  - Reads `appointments`
  - Reads related `patient_profiles`, `users`, `departments`
  - No writes
- Files touched:
  - `app/api/v1/routers/management/receptionist_management.py`
  - `app/services/clinical_service.py`
  - `app/utils/receptionist_serializers.py`

### `PATCH /api/v1/receptionist/appointments/{appointment_ref}`

- Router handler:
  - `app/api/v1/routers/management/receptionist_management.py:506`
  - `modify_appointment()`
- Main service:
  - `app/services/clinical_service.py:1583`
  - `ClinicalService.modify_opd_appointment()`
- Important downstream helpers:
  - `app/services/appointment_service.py:117` -> `AppointmentService.get_available_time_slots_for_doctor_user()`
- Workflow:
  1. Loads appointment by `appointment_ref`.
  2. Validates status and editability.
  3. Optionally resolves new doctor/department.
  4. Re-validates schedule availability if date/time changes.
  5. Updates and commits the appointment.
- Reads/writes:
  - Reads/writes `appointments`
  - Reads `users`, `departments`, doctor schedule data
- Files touched:
  - `app/api/v1/routers/management/receptionist_management.py`
  - `app/services/clinical_service.py`
  - `app/services/appointment_service.py`
  - `app/core/utils.py`

### `PATCH /api/v1/receptionist/appointments/{appointment_ref}/status`

- Router handler:
  - `app/api/v1/routers/management/receptionist_management.py:538`
  - `update_appointment_status()`
- Main service:
  - `app/services/clinical_service.py:1766`
  - `ClinicalService.update_opd_appointment_status()`
- Workflow:
  1. Loads appointment scoped to the receptionist hospital.
  2. Validates the requested status.
  3. Updates the appointment status and commits.
- Reads/writes:
  - Reads/writes `appointments`
- Files touched:
  - `app/api/v1/routers/management/receptionist_management.py`
  - `app/services/clinical_service.py`

### `PATCH /api/v1/receptionist/appointments/{appointment_ref}/cancel`

- Router handler:
  - `app/api/v1/routers/management/receptionist_management.py:551`
  - `cancel_appointment()`
- Main service:
  - `app/services/clinical_service.py:1800`
  - `ClinicalService.cancel_opd_appointment()`
- Workflow:
  1. Loads appointment.
  2. Stores cancellation reason/details.
  3. Updates status to cancelled and commits.
- Reads/writes:
  - Reads/writes `appointments`
- Files touched:
  - `app/api/v1/routers/management/receptionist_management.py`
  - `app/services/clinical_service.py`

### `DELETE /api/v1/receptionist/appointments/{appointment_ref}`

- Router handler:
  - `app/api/v1/routers/management/receptionist_management.py:564`
  - `delete_appointment()`
- Main service:
  - `app/services/clinical_service.py:1827`
  - `ClinicalService.delete_opd_appointment()`
- Workflow:
  1. Loads appointment.
  2. Deletes it from the write DB session.
  3. Commits the delete.
- Reads/writes:
  - Reads/writes `appointments`
- Files touched:
  - `app/api/v1/routers/management/receptionist_management.py`
  - `app/services/clinical_service.py`

### `POST /api/v1/receptionist/appointments/{appointment_ref}/check-in`

- Router handler:
  - `app/api/v1/routers/management/receptionist_management.py:578`
  - `check_in_patient()`
- Main service:
  - `app/services/clinical_service.py:1898`
  - `ClinicalService.check_in_patient()`
- Workflow:
  1. Loads appointment.
  2. Ensures the appointment is for today.
  3. Sets `checked_in_at`.
  4. Sets appointment status to `CHECKED_IN`.
  5. Commits and returns the check-in payload.
- Reads/writes:
  - Reads/writes `appointments`
  - Reads related `patient_profiles` and `users` for response payload
- Files touched:
  - `app/api/v1/routers/management/receptionist_management.py`
  - `app/services/clinical_service.py`

### `GET /api/v1/receptionist/patients`

- Router handler:
  - `app/api/v1/routers/management/receptionist_management.py:615`
  - `list_all_patients()`
- Main service:
  - `app/services/appointment_service.py:516`
  - `AppointmentService.search_patients()`
- Workflow:
  1. Opens tenant DB with `get_receptionist_tenant_db()`.
  2. Builds combined search term from `search` or `q`.
  3. Queries patient list scoped to the receptionist hospital.
  4. Returns paginated result.
- Reads/writes:
  - Reads `patient_profiles`
  - Reads `users`
  - No writes
- Files touched:
  - `app/api/v1/routers/management/receptionist_management.py`
  - `app/services/appointment_service.py`
  - `app/utils/hospital_id_resolve.py`

### `GET /api/v1/receptionist/patients/search`

- Router handler:
  - `app/api/v1/routers/management/receptionist_management.py:643`
  - `search_patients()`
- Main service:
  - `app/services/appointment_service.py:516`
  - `AppointmentService.search_patients()`
- Workflow:
  1. Opens tenant DB.
  2. Builds query from `search`, `q`, `phone`, `email`, `name`, `patient_id`, `mrn`.
  3. Returns matching patients plus pagination metadata.
- Reads/writes:
  - Reads `patient_profiles`
  - Reads `users`
  - No writes
- Files touched:
  - `app/api/v1/routers/management/receptionist_management.py`
  - `app/services/appointment_service.py`
  - `app/utils/hospital_id_resolve.py`

### `GET /api/v1/receptionist/patients/{patient_ref}/profile`

- Router handler:
  - `app/api/v1/routers/management/receptionist_management.py:697`
  - `get_patient_profile_for_schedule()`
- Main service:
  - `app/services/clinical_service.py:1136`
  - `ClinicalService.get_receptionist_patient_by_ref()`
- Serializer:
  - `app/utils/receptionist_serializers.py:110` -> `build_receptionist_patient_full_payload()`
- Workflow:
  1. Opens tenant clinical service.
  2. Resolves the patient by `patient_ref`.
  3. Expands patient registration payload for scheduling autofill.
- Reads/writes:
  - Reads `patient_profiles`
  - Reads `users`
  - No writes
- Files touched:
  - `app/api/v1/routers/management/receptionist_management.py`
  - `app/services/clinical_service.py`
  - `app/utils/receptionist_serializers.py`

### `POST /api/v1/receptionist/patient-documents/upload`

- Router handler:
  - `app/api/v1/routers/management/receptionist_management.py:721`
  - `receptionist_upload_patient_documents()`
- Router-local helpers:
  - `app/api/v1/routers/management/receptionist_management.py:141` -> `_normalize_patient_document_type()`
  - `app/api/v1/routers/management/receptionist_management.py:188` -> `_resolve_patient_for_documents()`
- Document-storage helpers:
  - `app/api/v1/routers/patient/patient_document_storage.py:77` -> `get_patient_by_ref()`
  - `app/api/v1/routers/patient/patient_document_storage.py:113` -> `get_upload_directory()`
  - `app/api/v1/routers/patient/patient_document_storage.py:131` -> `validate_file_type()`
  - `app/api/v1/routers/patient/patient_document_storage.py:148` -> `validate_file_size()`
  - `app/api/v1/routers/patient/patient_document_storage.py:157` -> `save_uploaded_file()`
- Workflow:
  1. Validates multipart payload and uploader/category.
  2. Resolves patient by PAT ref or UUID.
  3. Builds upload directory under `uploads/`.
  4. Validates each file.
  5. Saves each file to disk.
  6. Inserts `PatientDocument` row for each file.
  7. Commits transaction, or rolls back and deletes saved files on failure.
- Reads/writes:
  - Reads `patient_profiles`
  - Writes `patient_documents`
  - Writes uploaded files under the filesystem upload directory
- Files touched:
  - `app/api/v1/routers/management/receptionist_management.py`
  - `app/api/v1/routers/patient/patient_document_storage.py`
  - `app/core/utils.py`

### `GET /api/v1/receptionist/patients/{patient_ref}/documents`

- Router handler:
  - `app/api/v1/routers/management/receptionist_management.py:856`
  - `receptionist_list_patient_documents()`
- Router-local helper:
  - `app/api/v1/routers/management/receptionist_management.py:188` -> `_resolve_patient_for_documents()`
- Workflow:
  1. Resolves the patient in the tenant DB.
  2. Loads newest patient documents with uploader relation.
  3. Converts stored file path to public URL and returns document card payload.
- Reads/writes:
  - Reads `patient_profiles`
  - Reads `patient_documents`
  - Reads uploader `users`
  - No writes
- Files touched:
  - `app/api/v1/routers/management/receptionist_management.py`
  - `app/api/v1/routers/patient/patient_document_storage.py`
  - `app/core/utils.py`

### `GET /api/v1/receptionist/appointments/statistics`

- Router handler:
  - `app/api/v1/routers/management/receptionist_management.py:900`
  - `get_appointment_statistics()`
- Main service:
  - `app/services/clinical_service.py:1847`
  - `ClinicalService.get_opd_appointment_dashboard_stats()`
- Workflow:
  1. Validates receptionist access.
  2. Reads appointment rows for the target day.
  3. Aggregates totals by status/check-in state.
- Reads/writes:
  - Reads `appointments`
  - No writes
- Files touched:
  - `app/api/v1/routers/management/receptionist_management.py`
  - `app/services/clinical_service.py`

### `GET /api/v1/receptionist/quick-actions`

- Router handler:
  - `app/api/v1/routers/management/receptionist_management.py:931`
  - `get_quick_actions()`
- Main service:
  - `app/services/clinical_service.py:1847`
  - `ClinicalService.get_opd_appointment_dashboard_stats()`
- Workflow:
  1. Reuses the appointment dashboard stats service.
  2. Adds static `quick_links` in the router response.
- Reads/writes:
  - Reads `appointments`
  - No writes
- Files touched:
  - `app/api/v1/routers/management/receptionist_management.py`
  - `app/services/clinical_service.py`

### `GET /api/v1/receptionist/profile`

- Router handler:
  - `app/api/v1/routers/management/receptionist_management.py:972`
  - `get_receptionist_profile()`
- Router-local helper:
  - `app/api/v1/routers/management/receptionist_management.py:253` -> `_receptionist_profile_base_dict()`
- Workflow:
  1. Uses the platform DB session directly.
  2. Loads `ReceptionistProfile` and its `department`.
  3. Merges user-level fields and receptionist-profile fields into one response.
- Reads/writes:
  - Reads `users`
  - Reads `receptionist_profiles`
  - Reads `departments`
  - No writes
- Files touched:
  - `app/api/v1/routers/management/receptionist_management.py`
  - `app/models/receptionist.py`
  - `app/models/hospital.py`
  - `app/core/utils.py`

### `PATCH /api/v1/receptionist/profile`
### `PUT /api/v1/receptionist/profile`

- Router handler:
  - `app/api/v1/routers/management/receptionist_management.py:1036`
  - `update_receptionist_profile()`
- Router-local helpers:
  - `app/api/v1/routers/management/receptionist_management.py:227` -> `_receptionist_user_for_write()`
  - `app/api/v1/routers/management/receptionist_management.py:253` -> `_receptionist_profile_base_dict()`
- Workflow:
  1. Loads the current user record for writes.
  2. Loads `ReceptionistProfile`.
  3. Runs duplicate checks for email, phone, and employee ID.
  4. Updates `User`, `user_metadata`, and `ReceptionistProfile`.
  5. Reloads `Department` for response payload.
- Reads/writes:
  - Reads/writes `users`
  - Reads/writes `receptionist_profiles`
  - Reads `departments`
- Files touched:
  - `app/api/v1/routers/management/receptionist_management.py`
  - `app/models/receptionist.py`
  - `app/models/hospital.py`
  - `app/core/utils.py`

## Fast lookup by service area

### Doctor directory and department lookup

- `GET /api/v1/receptionist/doctors`
- `GET /api/v1/receptionist/doctors/search`
- `GET /api/v1/receptionist/doctors/dropdown`
- `GET /api/v1/receptionist/doctors/statistics`
- `GET /api/v1/receptionist/doctors/{doctor_id}`
- `GET /api/v1/receptionist/departments`
- `GET /api/v1/receptionist/departments/search`
- `GET /api/v1/receptionist/departments/dropdown`
- `GET /api/v1/receptionist/departments/statistics`
- `GET /api/v1/receptionist/departments/{department_id}`
- `GET /api/v1/receptionist/departments/{department_id}/doctors`
- `GET /api/v1/receptionist/departments/{department_id}/nurses`
- `GET /api/v1/receptionist/departments/{department_id}/beds`
- `GET /api/v1/receptionist/appointments/available-slots`
- `GET /api/v1/receptionist/appointments/queue`
- `GET /api/v1/receptionist/appointments/status-summary`

### Dashboard

- `GET /api/v1/receptionist/dashboard`
- `GET /api/v1/receptionist/quick-actions`
- `GET /api/v1/receptionist/appointments/statistics`

### Patient registration and patient records

- `POST /api/v1/receptionist/patients/register`
- `PATCH /api/v1/receptionist/patients/{patient_ref}`
- `PUT /api/v1/receptionist/patients/{patient_ref}`
- `GET /api/v1/receptionist/patients`
- `GET /api/v1/receptionist/patients/search`
- `GET /api/v1/receptionist/patients/{patient_ref}/profile`

### Appointments

- `POST /api/v1/receptionist/appointments/schedule`
- `GET /api/v1/receptionist/appointments/today`
- `GET /api/v1/receptionist/appointments/{appointment_ref}`
- `PATCH /api/v1/receptionist/appointments/{appointment_ref}`
- `PATCH /api/v1/receptionist/appointments/{appointment_ref}/status`
- `PATCH /api/v1/receptionist/appointments/{appointment_ref}/cancel`
- `DELETE /api/v1/receptionist/appointments/{appointment_ref}`
- `POST /api/v1/receptionist/appointments/{appointment_ref}/check-in`

### Documents

- `POST /api/v1/receptionist/patient-documents/upload`
- `GET /api/v1/receptionist/patients/{patient_ref}/documents`

### Profile

- `GET /api/v1/receptionist/profile`
- `PATCH /api/v1/receptionist/profile`
- `PUT /api/v1/receptionist/profile`

### Related staff doctor schedule endpoints

- `GET /api/v1/staff/doctor-schedules/{doctor_name}`
- `GET /api/v1/staff/doctor-schedules/{doctor_name}/check-slots`
- `POST /api/v1/staff/doctor-schedules/{doctor_name}`
- `PUT /api/v1/staff/doctor-schedules/slots/{schedule_id}`
- `DELETE /api/v1/staff/doctor-schedules/slots/{schedule_id}`

## Additional receptionist-facing directory workflows

These endpoints live in `app/api/v1/routers/management/receptionist_directory.py`.

### `GET /api/v1/receptionist/doctors`

- Router handler:
  - `app/api/v1/routers/management/receptionist_directory.py:257`
  - `get_all_doctors()`
- Main helper:
  - `app/api/v1/routers/management/receptionist_directory.py:166`
  - `_doctor_query()`
- Workflow:
  1. Resolves receptionist hospital context.
  2. Filters doctors by department, status, availability, or keyword.
  3. Resolves department display across sessions.
  4. Returns doctor directory rows.
- Reads/writes:
  - Reads `doctor_profiles`
  - Reads `users`
  - Reads `roles`, `user_roles`
  - Reads `staff_department_assignments`
  - Reads same-day `appointments` to derive active/in-consultation state
  - No writes
- Files touched:
  - `app/api/v1/routers/management/receptionist_directory.py`
  - `app/utils/hospital_id_resolve.py`
  - `app/utils/doctor_department_resolve.py`

### `GET /api/v1/receptionist/doctors/search`

- Router handler:
  - `app/api/v1/routers/management/receptionist_directory.py:280`
  - `search_doctors()`
- Main helper:
  - `app/api/v1/routers/management/receptionist_directory.py:166`
  - `_doctor_query()`
- Reads/writes:
  - Reads `doctor_profiles`, `users`, `roles`, `user_roles`, `staff_department_assignments`, `appointments`
  - No writes
- Files touched:
  - `app/api/v1/routers/management/receptionist_directory.py`
  - `app/utils/hospital_id_resolve.py`
  - `app/utils/doctor_department_resolve.py`

### `GET /api/v1/receptionist/doctors/dropdown`

- Router handler:
  - `app/api/v1/routers/management/receptionist_directory.py:294`
  - `get_doctor_dropdown()`
- Main helper:
  - `app/api/v1/routers/management/receptionist_directory.py:166`
  - `_doctor_query()`
- Reads/writes:
  - Reads `doctor_profiles`, `users`, `roles`, `user_roles`, `staff_department_assignments`, `appointments`
  - No writes
- Files touched:
  - `app/api/v1/routers/management/receptionist_directory.py`
  - `app/utils/hospital_id_resolve.py`
  - `app/utils/doctor_department_resolve.py`

### `GET /api/v1/receptionist/doctors/statistics`

- Router handler:
  - `app/api/v1/routers/management/receptionist_directory.py:324`
  - `get_doctor_statistics()`
- Main helper:
  - `app/api/v1/routers/management/receptionist_directory.py:166`
  - `_doctor_query()`
- Workflow:
  1. Loads doctor directory rows.
  2. Aggregates totals by availability and status.
- Reads/writes:
  - Reads `doctor_profiles`, `users`, `roles`, `user_roles`, `staff_department_assignments`, `appointments`
  - No writes
- Files touched:
  - `app/api/v1/routers/management/receptionist_directory.py`
  - `app/utils/hospital_id_resolve.py`
  - `app/utils/doctor_department_resolve.py`

### `GET /api/v1/receptionist/doctors/{doctor_id}`

- Router handler:
  - `app/api/v1/routers/management/receptionist_directory.py:345`
  - `get_doctor_by_id()`
- Main helpers:
  - `app/api/v1/routers/management/receptionist_directory.py:124` -> `_doctor_payload()`
  - `app/utils/doctor_department_resolve.py` -> `resolve_doctor_departments_multi_session()`
- Reads/writes:
  - Reads `doctor_profiles`
  - Reads `users`
  - Reads same-day `appointments`
  - Reads department assignment data
  - No writes
- Files touched:
  - `app/api/v1/routers/management/receptionist_directory.py`
  - `app/utils/hospital_id_resolve.py`
  - `app/utils/doctor_department_resolve.py`

### `GET /api/v1/receptionist/departments`

- Router handler:
  - `app/api/v1/routers/management/receptionist_directory.py:481`
  - `get_all_departments()`
- Main helper:
  - `app/api/v1/routers/management/receptionist_directory.py:445`
  - `_department_query()`
- Workflow:
  1. Resolves hospital context.
  2. Loads department rows.
  3. Counts doctors, nurses, and active admissions per department.
  4. Returns directory payload.
- Reads/writes:
  - Reads `departments`
  - Reads `doctor_profiles`
  - Reads `nurse_profiles`
  - Reads `admissions`
  - No writes
- Files touched:
  - `app/api/v1/routers/management/receptionist_directory.py`
  - `app/utils/hospital_id_resolve.py`

### `GET /api/v1/receptionist/departments/search`

- Router handler:
  - `app/api/v1/routers/management/receptionist_directory.py:503`
  - `search_departments()`
- Main helper:
  - `app/api/v1/routers/management/receptionist_directory.py:445`
  - `_department_query()`
- Reads/writes:
  - Reads `departments`, `doctor_profiles`, `nurse_profiles`, `admissions`
  - No writes
- Files touched:
  - `app/api/v1/routers/management/receptionist_directory.py`
  - `app/utils/hospital_id_resolve.py`

### `GET /api/v1/receptionist/departments/dropdown`

- Router handler:
  - `app/api/v1/routers/management/receptionist_directory.py:517`
  - `get_department_dropdown()`
- Main helper:
  - `app/api/v1/routers/management/receptionist_directory.py:445`
  - `_department_query()`
- Reads/writes:
  - Reads `departments`, `doctor_profiles`, `nurse_profiles`, `admissions`
  - No writes
- Files touched:
  - `app/api/v1/routers/management/receptionist_directory.py`
  - `app/utils/hospital_id_resolve.py`

### `GET /api/v1/receptionist/departments/statistics`

- Router handler:
  - `app/api/v1/routers/management/receptionist_directory.py:539`
  - `get_department_statistics()`
- Main helper:
  - `app/api/v1/routers/management/receptionist_directory.py:445`
  - `_department_query()`
- Reads/writes:
  - Reads `departments`, `doctor_profiles`, `nurse_profiles`, `admissions`
  - No writes
- Files touched:
  - `app/api/v1/routers/management/receptionist_directory.py`
  - `app/utils/hospital_id_resolve.py`

### `GET /api/v1/receptionist/departments/{department_id}`

- Router handler:
  - `app/api/v1/routers/management/receptionist_directory.py:560`
  - `get_department_by_id()`
- Main helper:
  - `app/api/v1/routers/management/receptionist_directory.py:445`
  - `_department_query()`
- Reads/writes:
  - Reads `departments`, `doctor_profiles`, `nurse_profiles`, `admissions`
  - No writes
- Files touched:
  - `app/api/v1/routers/management/receptionist_directory.py`
  - `app/utils/hospital_id_resolve.py`

### `GET /api/v1/receptionist/departments/{department_id}/doctors`

- Router handler:
  - `app/api/v1/routers/management/receptionist_directory.py:578`
  - `get_department_doctors()`
- Main helper:
  - `app/api/v1/routers/management/receptionist_directory.py:166`
  - `_doctor_query()`
- Reads/writes:
  - Reads `doctor_profiles`, `users`, `roles`, `user_roles`, `staff_department_assignments`, `appointments`
  - No writes
- Files touched:
  - `app/api/v1/routers/management/receptionist_directory.py`
  - `app/utils/hospital_id_resolve.py`
  - `app/utils/doctor_department_resolve.py`

### `GET /api/v1/receptionist/departments/{department_id}/nurses`

- Router handler:
  - `app/api/v1/routers/management/receptionist_directory.py:589`
  - `get_department_nurses()`
- Reads/writes:
  - Reads `nurse_profiles`
  - Reads `users`
  - No writes
- Files touched:
  - `app/api/v1/routers/management/receptionist_directory.py`
  - `app/utils/hospital_id_resolve.py`

### `GET /api/v1/receptionist/departments/{department_id}/beds`

- Router handler:
  - `app/api/v1/routers/management/receptionist_directory.py:624`
  - `get_department_beds()`
- Workflow:
  1. Loads department row.
  2. Counts active admissions in that department.
  3. Computes occupied and available beds.
- Reads/writes:
  - Reads `departments`
  - Reads `admissions`
  - No writes
- Files touched:
  - `app/api/v1/routers/management/receptionist_directory.py`
  - `app/utils/hospital_id_resolve.py`

### `GET /api/v1/receptionist/appointments/available-slots`

- Router handler:
  - `app/api/v1/routers/management/receptionist_directory.py:660`
  - `get_available_slots()`
- Main helpers:
  - `app/api/v1/routers/management/receptionist_directory.py:166` -> `_doctor_query()`
  - `app/services/appointment_service.py:117` -> `get_available_time_slots_for_doctor_user()`
- Workflow:
  1. Resolves doctor by `doctorId` or exact `doctorName`.
  2. Calls `AppointmentService` to build slots.
  3. Returns appointment-ready slot list for the requested date.
- Reads/writes:
  - Reads `doctor_profiles`
  - Reads `users`
  - Reads `doctor_schedules`
  - Reads `appointments`
  - No writes
- Files touched:
  - `app/api/v1/routers/management/receptionist_directory.py`
  - `app/services/appointment_service.py`
  - `app/utils/hospital_id_resolve.py`

### `GET /api/v1/receptionist/appointments/queue`

- Router handler:
  - `app/api/v1/routers/management/receptionist_directory.py:717`
  - `get_appointment_queue()`
- Main helpers:
  - `app/api/v1/routers/management/receptionist_directory.py:702` -> `_appointments_for_date()`
  - `app/utils/receptionist_serializers.py:201` -> `serialize_opd_appointment_full()`
- Reads/writes:
  - Reads `appointments`
  - Reads `patient_profiles`
  - Reads `users`
  - Reads `departments`
  - No writes
- Files touched:
  - `app/api/v1/routers/management/receptionist_directory.py`
  - `app/utils/hospital_id_resolve.py`
  - `app/utils/receptionist_serializers.py`

### `GET /api/v1/receptionist/appointments/status-summary`

- Router handler:
  - `app/api/v1/routers/management/receptionist_directory.py:731`
  - `get_appointment_status_summary()`
- Workflow:
  1. Aggregates appointment counts by status for the requested day.
  2. Derives summary fields such as waiting/completed/cancelled.
- Reads/writes:
  - Reads `appointments`
  - No writes
- Files touched:
  - `app/api/v1/routers/management/receptionist_directory.py`
  - `app/utils/hospital_id_resolve.py`

## Related staff doctor schedule workflows

These endpoints are not under `/receptionist`, but they are used by receptionist and nurse
users for doctor availability management.

Router file:

- `app/api/v1/routers/management/staff_doctor_schedules.py`
- Prefix: `/staff/doctor-schedules`

Main model/table:

- `app/models/schedule.py:11`
- `DoctorSchedule`
- Table: `doctor_schedules`

### `GET /api/v1/staff/doctor-schedules/{doctor_name}`

- Router handler:
  - `app/api/v1/routers/management/staff_doctor_schedules.py:76`
  - `staff_get_doctor_schedule_template()`
- Main service:
  - `app/services/doctor_service.py:1045`
  - `DoctorService.get_schedule_slots_for_target_doctor()`
- Reads/writes:
  - Reads `doctor_schedules`
  - Reads `users`
  - Reads `staff_department_assignments`
  - Reads `doctor_profiles`, `receptionist_profiles`, `nurse_profiles`
  - No writes
- Files touched:
  - `app/api/v1/routers/management/staff_doctor_schedules.py`
  - `app/services/doctor_service.py`
  - `app/models/schedule.py`

### `GET /api/v1/staff/doctor-schedules/{doctor_name}/check-slots`

- Router handler:
  - `app/api/v1/routers/management/staff_doctor_schedules.py:51`
  - `staff_check_doctor_available_slots()`
- Main services:
  - `app/services/doctor_service.py:879`
  - `DoctorService.get_target_doctor_in_hospital_for_staff_by_name()`
  - `app/services/appointment_service.py:117`
  - `AppointmentService.get_available_time_slots_for_doctor_user()`
- Reads/writes:
  - Reads `doctor_schedules`
  - Reads `appointments`
  - Reads `users`
  - Reads `staff_department_assignments`
  - Reads `doctor_profiles`, `receptionist_profiles`, `nurse_profiles`
  - No writes
- Files touched:
  - `app/api/v1/routers/management/staff_doctor_schedules.py`
  - `app/services/doctor_service.py`
  - `app/services/appointment_service.py`
  - `app/models/schedule.py`

### `POST /api/v1/staff/doctor-schedules/{doctor_name}`

- Router handler:
  - `app/api/v1/routers/management/staff_doctor_schedules.py:88`
  - `staff_create_doctor_schedule_slot()`
- Main service:
  - `app/services/doctor_service.py:1083`
  - `DoctorService.create_schedule_slot_for_staff()`
- Workflow:
  1. Resolves target doctor in the same department.
  2. Validates date/time and duplicate schedule conflicts.
  3. Inserts `DoctorSchedule`.
  4. Commits the new slot.
- Reads/writes:
  - Reads `users`
  - Reads `staff_department_assignments`
  - Reads `doctor_profiles`, `receptionist_profiles`, `nurse_profiles`
  - Reads existing `doctor_schedules`
  - Writes `doctor_schedules`
- Files touched:
  - `app/api/v1/routers/management/staff_doctor_schedules.py`
  - `app/services/doctor_service.py`
  - `app/models/schedule.py`

### `PUT /api/v1/staff/doctor-schedules/slots/{schedule_id}`

- Router handler:
  - `app/api/v1/routers/management/staff_doctor_schedules.py:24`
  - `staff_update_doctor_schedule_slot()`
- Main service:
  - `app/services/doctor_service.py:1156`
  - `DoctorService.update_schedule_slot_for_staff()`
- Reads/writes:
  - Reads `doctor_schedules`
  - Reads `users`
  - Reads `staff_department_assignments`
  - Reads `doctor_profiles`
  - Writes `doctor_schedules`
- Files touched:
  - `app/api/v1/routers/management/staff_doctor_schedules.py`
  - `app/services/doctor_service.py`
  - `app/models/schedule.py`

### `DELETE /api/v1/staff/doctor-schedules/slots/{schedule_id}`

- Router handler:
  - `app/api/v1/routers/management/staff_doctor_schedules.py:39`
  - `staff_delete_doctor_schedule_slot()`
- Main service:
  - `app/services/doctor_service.py:1203`
  - `DoctorService.delete_schedule_slot_for_staff()`
- Reads/writes:
  - Reads `doctor_schedules`
  - Reads `users`
  - Reads `staff_department_assignments`
  - Reads `doctor_profiles`
  - Deletes from `doctor_schedules`
- Files touched:
  - `app/api/v1/routers/management/staff_doctor_schedules.py`
  - `app/services/doctor_service.py`
  - `app/models/schedule.py`
