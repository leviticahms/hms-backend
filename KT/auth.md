# KT — Authentication Module

Knowledge-transfer doc for the **Authentication** endpoints (the `/api/v1/auth` Swagger group).

For every endpoint it shows the **full file flow**: which router function receives it, which
service class/method runs the logic, which schema classes validate input/output, which model
classes are touched, and which security/dependency helpers are involved — plus the
step-by-step flow.

> Paths are dotted module names. `Class.method` means a method on that class.
> Line numbers are for the current branch and may shift over time.

---

## Endpoints covered

1. `POST /api/v1/auth/login` — Login
2. `POST /api/v1/auth/super-admin/hospitals` — Create Hospital
3. `POST /api/v1/auth/super-admin/hospitals/{hospital_id}/admins` — Create Hospital Admin
4. `POST /api/v1/auth/hospital-admin/change-password` — Hospital Admin Change Password
5. `POST /api/v1/auth/staff/change-password` — Staff Change Password
6. `GET /api/v1/auth/hospitals` — Get Available Hospitals
7. `POST /api/v1/auth/patient/register` — Patient Register
8. `POST /api/v1/auth/patient/verify-otp` — Patient Verify OTP
9. `POST /api/v1/auth/patient/login` — Patient Login
10. `POST /api/v1/auth/patient/forgot-password` — Patient Forgot Password
11. `POST /api/v1/auth/patient/reset-password` — Patient Reset Password
12. `POST /api/v1/auth/patient/change-password` — Patient Change Password
13. `POST /api/v1/auth/logout` — Logout
14. `GET /api/v1/auth/me` — Get Current User Info

---

## Module file map (what each file is for)

| Layer | Module | File | Role in this module |
| --- | --- | --- | --- |
| Router | `app.api.v1.auth` | `app/api/v1/auth.py` | All 14 `/auth` endpoints (login, registration, passwords, `/me`, logout) |
| Service | `app.services.auth_service` | `app/services/auth_service.py` | `AuthService` — all auth business logic + `PasswordValidator`, `EmailDomainValidator` |
| Service | `app.services.otp_service` | `app/services/otp_service.py` | `OTPService` — email OTP generate/verify |
| Service | `app.services.email_service` | `app/services/email_service.py` | `EmailService` — send verification/reset emails |
| Schema | `app.schemas.auth` | `app/schemas/auth.py` | Request/response Pydantic models |
| Schema | `app.schemas.response` | `app/schemas/response.py` | `SuccessResponse` / `APIResponse` envelope |
| Security | `app.core.security` | `app/core/security.py` | `SecurityManager` (hash/JWT) + `get_current_user` |
| Deps/RBAC | `app.api.deps` | `app/api/deps.py` | `require_super_admin`, `require_hospital_admin`, `require_staff`, `require_patient` |
| Model | `app.models.user` | `app/models/user.py` | `User`, `Role`, `user_roles` |
| Model | `app.models.patient` | `app/models/patient.py` | `PatientProfile` (created on patient registration) |
| Model | `app.models.tenant` | `app/models/tenant.py` | `Hospital` (resolved/validated during login & registration) |
| Model | `app.models.password_history` | `app/models/password_history.py` | `PasswordHistory` (blocks password reuse) |

---

## High-level module flow

```
HTTP request
   │
   ▼
Router (app/api/v1/auth.py)
   │  - validates body with app.schemas.auth.*
   │  - applies RBAC via app.api.deps.* or app.core.security.get_current_user
   ▼
AuthService (app/services/auth_service.py)
   │  - SecurityManager: hash/verify password, create JWT
   │  - PasswordValidator: password policy
   │  - OTPService + EmailService: email OTP flows
   │  - reads/writes User / Role / PatientProfile / Hospital / PasswordHistory
   ▼
Platform DB (and tenant DB mirror where applicable)
   │
   ▼
Response wrapped by app.schemas.response.SuccessResponse
```

All 14 endpoints use the platform DB session (`get_platform_db_session`). Some service
methods also mirror credentials into the hospital **tenant DB** (password change, patient
email verification).

---

## Endpoint-by-endpoint flow

### 1. `POST /api/v1/auth/login`
- Router: `app.api.v1.auth.login` (`auth.py:41`)
- Schema in: `app.schemas.auth.LoginCreate` · Schema out: `app.schemas.auth.AuthOut`
- Service: `AuthService.staff_admin_super_admin_login` (`auth_service.py:861`)
- Flow:
  1. Normalize email; if it matches `SUPERADMIN_EMAIL`, ensure the bootstrap account
     (`app.services.superadmin_bootstrap.ensure_superadmin_account`).
  2. Load user via `AuthService._get_user_by_email`; self-heal from tenant DB if missing
     (`_heal_platform_auth_row_from_tenant_by_email`).
  3. Block patient accounts via `app.core.role_aliases.user_can_use_staff_login`.
  4. `SecurityManager.verify_password`; on failure `_log_failed_login`.
  5. Check `User.status == ACTIVE` and `_enforce_hospital_login_access` (hospital active + subscription).
  6. `AuthService._generate_auth_response` → `SecurityManager.create_access_token` /
     `create_refresh_token`, update `User.last_login`.
- Models: `User`, `Role`, `user_roles`, `Hospital`.

### 2. `POST /api/v1/auth/super-admin/hospitals`
- Router: `app.api.v1.auth.create_hospital` (`auth.py:60`)
- RBAC: `app.api.deps.require_super_admin`
- Schema in: `app.schemas.auth.HospitalCreate`
- Service: `AuthService.create_hospital` (`auth_service.py:279`)
- Flow: validate super-admin → create `Hospital` row (+ tenant provisioning data) → return hospital info.
- Models: `Hospital`.

### 3. `POST /api/v1/auth/super-admin/hospitals/{hospital_id}/admins`
- Router: `app.api.v1.auth.create_hospital_admin` (`auth.py:81`)
- RBAC: `app.api.deps.require_super_admin`
- Schema in: `app.schemas.auth.HospitalAdminCreate` · Schema out: `app.schemas.auth.HospitalAdminOut`
- Service: `AuthService.create_hospital_admin` (`auth_service.py:416`)
- Flow: create admin `User` with `SecurityManager` (temp password), assign HOSPITAL_ADMIN
  `Role` via `user_roles`, optionally email credentials (`EmailService`).
- Models: `User`, `Role`, `user_roles`, `Hospital`.

### 4. `POST /api/v1/auth/hospital-admin/change-password`
- Router: `app.api.v1.auth.hospital_admin_change_password` (`auth.py:114`)
- RBAC: `app.api.deps.require_hospital_admin`
- Schema in: `app.schemas.auth.PasswordChangeUpdate`
- Service: `AuthService.change_password` (`auth_service.py:1120`)
- Flow: `_get_user_by_id` → `SecurityManager.verify_password` (current) →
  `PasswordValidator.validate_password` → `_is_password_reused` (`PasswordHistory`) →
  `SecurityManager.hash_password` → update platform `User`, mirror hash to tenant `User`
  → `_save_password_history`.
- Models: `User`, `Hospital`, `PasswordHistory`.

### 5. `POST /api/v1/auth/staff/change-password`
- Router: `app.api.v1.auth.staff_change_password` (`auth.py:143`)
- RBAC: `app.api.deps.require_staff`
- Schema in: `app.schemas.auth.PasswordChangeUpdate`
- Service: `AuthService.change_password` (`auth_service.py:1120`) — same flow as #4.

### 6. `GET /api/v1/auth/hospitals`
- Router: `app.api.v1.auth.get_available_hospitals` (`auth.py:172`) — public, no auth
- Schema out: `app.schemas.auth.HospitalOut`
- Service: `AuthService.get_available_hospitals` (`auth_service.py:1216`)
- Flow: list active hospitals available for patient self-registration.
- Models: `Hospital`.

### 7. `POST /api/v1/auth/patient/register`
- Router: `app.api.v1.auth.patient_register` (`auth.py:190`)
- Schema in: `app.schemas.auth.PatientRegistrationCreate`
- Service: `AuthService.register_patient` (`auth_service.py:649`)
- Flow:
  1. `PasswordValidator.validate_password`.
  2. Reject duplicate email (`_get_user_by_email`).
  3. `_resolve_hospital_for_patient` → create `User` (status PENDING) with `SecurityManager.hash_password`.
  4. Assign PATIENT `Role` via `user_roles` (creates the role if missing).
  5. Create `PatientProfile` with unique `patient_ref` (`app.core.utils.generate_patient_ref`).
  6. `OTPService.generate_otp` + `EmailService.send_verification_email`.
- Models: `User`, `Role`, `user_roles`, `PatientProfile`, `Hospital`.

### 8. `POST /api/v1/auth/patient/verify-otp`
- Router: `app.api.v1.auth.patient_verify_otp` (`auth.py:209`)
- Schema in: `app.schemas.auth.OTPVerificationCreate`
- Service: `AuthService.verify_email` (`auth_service.py:1015`)
- Flow: `OTPService.verify_otp("email_verification")` → set `User.status = ACTIVE`,
  `email_verified = True` → if hospital has a tenant DB, mirror via
  `_mirror_platform_patient_bundle_to_tenant`.
- Models: `User`, `Hospital`, `PatientProfile`.

### 9. `POST /api/v1/auth/patient/login`
- Router: `app.api.v1.auth.patient_login` (`auth.py:231`)
- Schema in: `app.schemas.auth.LoginCreate` · Schema out: `app.schemas.auth.AuthOut`
- Service: `AuthService.patient_login` (`auth_service.py:929`)
- Flow: `_get_user_by_email` (heal from tenant if needed) → require PATIENT role →
  require `email_verified` → `SecurityManager.verify_password` → `_generate_auth_response`.
- Models: `User`, `Role`, `user_roles`.

### 10. `POST /api/v1/auth/patient/forgot-password`
- Router: `app.api.v1.auth.patient_forgot_password` (`auth.py:249`)
- Schema in: `app.schemas.auth.ForgotPasswordCreate`
- Service: `AuthService.forgot_password` (`auth_service.py:1061`)
- Flow: `OTPService.generate_otp("password_reset")` → `EmailService.send_password_reset_email`
  (always returns a generic message to avoid email enumeration).
- Models: `User`.

### 11. `POST /api/v1/auth/patient/reset-password`
- Router: `app.api.v1.auth.patient_reset_password` (`auth.py:267`)
- Schema in: `app.schemas.auth.PasswordResetCreate`
- Service: `AuthService.reset_password` (`auth_service.py:1079`)
- Flow: `OTPService.verify_otp("password_reset")` → `PasswordValidator.validate_password` →
  `_is_password_reused` → `SecurityManager.hash_password` → update `User` → `_save_password_history`.
- Models: `User`, `PasswordHistory`.

### 12. `POST /api/v1/auth/patient/change-password`
- Router: `app.api.v1.auth.patient_change_password` (`auth.py:289`)
- RBAC: `app.api.deps.require_patient`
- Schema in: `app.schemas.auth.PasswordChangeUpdate`
- Service: `AuthService.change_password` (`auth_service.py:1120`) — same flow as #4.

### 13. `POST /api/v1/auth/logout`
- Router: `app.api.v1.auth.logout` (`auth.py:318`)
- RBAC: `app.core.security.get_current_user`
- Flow: stateless logout (token blacklisting is a TODO pending Redis). No service call.

### 14. `GET /api/v1/auth/me`
- Router: `app.api.v1.auth.get_current_user_info` (`auth.py:334`)
- RBAC: `app.core.security.get_current_user`
- Schema out: `app.schemas.auth.UserInfoOut`
- Service: `AuthService.get_current_user_info` (`auth_service.py:1240`)
- Flow: assemble current user's profile + roles + hospital context.
- Models: `User`, `Role`, `Hospital`.

---

## Shared helpers used across endpoints

- `AuthService._get_user_by_email` / `_get_user_by_id` — user lookup with eager roles.
- `AuthService._generate_auth_response` — builds JWT payload (roles + permissions) and tokens.
- `AuthService._heal_platform_auth_row_from_tenant_by_email` /
  `_heal_platform_patient_from_tenant_by_email` — self-heal platform auth row from tenant DB.
- `PasswordValidator.validate_password` (`auth_service.py:80`) — password policy.
- `EmailDomainValidator` (`auth_service.py:36`) — staff email-domain rules.
- `SecurityManager` (`app/core/security.py:57`) — `hash_password`, `verify_password`,
  `create_access_token`, `create_refresh_token`, `verify_token`.
- `get_current_user` (`app/core/security.py:285`) — bearer-token → `User`.

## Unused in this module (not referenced by any of these endpoints)

Defined in `app/schemas/auth.py` but never imported/returned by these APIs:
`MessageResponse` (`:137`), `AuthResponse` (`:143`), `HospitalAdminResponse` (`:152`).
