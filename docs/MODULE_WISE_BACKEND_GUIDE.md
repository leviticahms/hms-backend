# Module-Wise Backend Guide

This guide documents the backend one module at a time. For each module it lists:

- the Python module name (dotted import path)
- the purpose of that file inside the module
- only files/code that are actually wired into the live Swagger APIs

Anything defined but never used by the APIs is called out under "Unused / safe to remove"
so the module stays clean.

Line numbers reflect the current branch and may drift as code changes.

---

# 1. Authentication

Handles every way a user proves identity and manages credentials: login for staff/admins,
patient self-registration with email OTP, password change/forgot/reset, optional TOTP 2FA,
and the shared building blocks (JWT, password hashing, RBAC dependencies) that every other
module relies on.

All endpoints live under the `/api/v1/auth` prefix.

## 1.1 Routers (HTTP entrypoints)

### `app.api.v1.auth`
- File: `app/api/v1/auth.py`
- Purpose: Main authentication router. Exposes login, super-admin hospital/admin creation,
  password change for admin/staff/patient, patient registration + OTP verification + login,
  forgot/reset password, logout, and `/me`. Delegates all logic to `AuthService`.

### `app.api.v1.routers.auth_2fa`
- File: `app/api/v1/routers/auth_2fa.py`
- Purpose: Two-Factor Authentication (TOTP, RFC 6238) router under `/auth/2fa`. Handles
  enrollment (setup + verify), login-time validation, and disabling 2FA. Delegates crypto
  to `TOTPService`.

## 1.2 Service layer (business logic)

### `app.services.auth_service`
- File: `app/services/auth_service.py`
- Purpose: Core authentication logic. Registration, login for each user type, password
  management, OTP-based email verification/reset, email-domain validation, hospital/admin/
  staff creation, and platform↔tenant user mirroring. Also contains `PasswordValidator` and
  `EmailDomainValidator` helpers.

### `app.services.totp_service`
- File: `app/services/totp_service.py`
- Purpose: TOTP secret generation, provisioning URI + QR code creation, and 6-digit code
  verification for 2FA. Wraps the `pyotp` library.

### `app.services.otp_service`
- File: `app/services/otp_service.py`
- Purpose: Generates and verifies short-lived email OTP codes used by patient registration,
  email verification, and forgot/reset-password flows (Redis-backed in production).

### `app.services.email_service`
- File: `app/services/email_service.py`
- Purpose: Sends auth-related emails (OTP codes, credentials). Used by `AuthService` during
  registration and password reset. (Shared with other modules.)

## 1.3 Schemas (request/response contracts)

### `app.schemas.auth`
- File: `app/schemas/auth.py`
- Purpose: Pydantic request/response models for the auth endpoints.
- Used by the Swagger APIs:
  - `LoginCreate` — login / patient-login body
  - `PasswordChangeUpdate` — change-password body (admin/staff/patient)
  - `HospitalCreate` — super-admin create-hospital body
  - `HospitalAdminCreate` — super-admin create-admin body
  - `PatientRegistrationCreate` — patient self-registration body
  - `OTPVerificationCreate` — verify-OTP body
  - `ForgotPasswordCreate` — forgot-password body
  - `PasswordResetCreate` — reset-password body
  - `AuthOut` — login response payload
  - `HospitalAdminOut` — created-admin response
  - `UserInfoOut` — `/me` response
  - `HospitalOut` — public hospital list response

### `app.schemas.response`
- File: `app/schemas/response.py`
- Purpose: Generic `SuccessResponse` / `APIResponse` envelope wrapping every auth response.
  (Shared across all modules.)

## 1.4 Dependencies, security & RBAC (shared building blocks)

### `app.core.security`
- File: `app/core/security.py`
- Purpose: Low-level security primitives. `SecurityManager` (bcrypt password hashing,
  temp-password generation, JWT access/refresh token creation and verification) and
  `get_current_user` (decodes the bearer token into a `User`). The foundation for all auth.

### `app.api.deps`
- File: `app/api/deps.py`
- Purpose: Centralized RBAC dependencies used as FastAPI `Depends(...)` guards on endpoints:
  `require_super_admin`, `require_hospital_admin`, `require_staff`, `require_patient`,
  `require_receptionist`, etc. Used by the auth router and every protected module.

### `app.dependencies.auth`
- File: `app/dependencies/auth.py`
- Purpose: Additional role/context dependencies (e.g. `get_current_patient`,
  `require_hospital_context`, `require_pharmacy_staff`). Consumed mainly by patient and
  pharmacy modules; part of the shared auth surface.

## 1.5 Models (persistence)

### `app.models.user`
- File: `app/models/user.py`
- Purpose: `User`, `Role`, and the `user_roles` association used by login, RBAC, and `/me`.
  Also holds the `totp_secret` / `totp_enabled` columns for 2FA.

### `app.models.password_history`
- File: `app/models/password_history.py`
- Purpose: `PasswordHistory` rows so password change/reset can reject reuse of recent passwords.

## 1.6 Endpoints (Swagger surface)

| Method | Path | Handler | Purpose |
| --- | --- | --- | --- |
| POST | `/api/v1/auth/login` | `login` | Unified login for super admin, hospital admin, and staff |
| POST | `/api/v1/auth/super-admin/hospitals` | `create_hospital` | Super admin creates a hospital |
| POST | `/api/v1/auth/super-admin/hospitals/{hospital_id}/admins` | `create_hospital_admin` | Super admin creates a hospital admin (returns temp password) |
| POST | `/api/v1/auth/hospital-admin/change-password` | `hospital_admin_change_password` | Hospital admin changes own password |
| POST | `/api/v1/auth/staff/change-password` | `staff_change_password` | Staff changes own password |
| GET | `/api/v1/auth/hospitals` | `get_available_hospitals` | Public list of hospitals for patient registration |
| POST | `/api/v1/auth/patient/register` | `patient_register` | Patient self-registration; sends email OTP |
| POST | `/api/v1/auth/patient/verify-otp` | `patient_verify_otp` | Activate patient account via email OTP |
| POST | `/api/v1/auth/patient/login` | `patient_login` | Patient login (requires verified email) |
| POST | `/api/v1/auth/patient/forgot-password` | `patient_forgot_password` | Send password-reset OTP to patient email |
| POST | `/api/v1/auth/patient/reset-password` | `patient_reset_password` | Reset patient password with OTP |
| POST | `/api/v1/auth/patient/change-password` | `patient_change_password` | Authenticated patient changes password |
| POST | `/api/v1/auth/logout` | `logout` | Universal logout for all user types |
| GET | `/api/v1/auth/me` | `get_current_user_info` | Current authenticated user info |
| POST | `/api/v1/auth/2fa/setup` | `setup_totp` | Generate TOTP secret + QR (2FA enrollment step 1) |
| POST | `/api/v1/auth/2fa/verify` | `verify_and_enable_totp` | Verify code and enable 2FA (step 2) |
| POST | `/api/v1/auth/2fa/validate` | `validate_totp_on_login` | Validate TOTP during login |
| DELETE | `/api/v1/auth/2fa/disable` | `disable_totp` | Disable 2FA (password confirmation) |

## 1.7 Typical flows

- Staff/Admin login: `POST /auth/login` → `AuthService.staff_admin_super_admin_login` →
  verify password (`SecurityManager`) → issue JWT (`AuthOut`). If 2FA enabled, finish with
  `POST /auth/2fa/validate`.
- Patient onboarding: `POST /auth/patient/register` → email OTP (`OTPService` + `EmailService`)
  → `POST /auth/patient/verify-otp` → `POST /auth/patient/login`.
- Password reset: `POST /auth/patient/forgot-password` → OTP email →
  `POST /auth/patient/reset-password` (reuse blocked via `PasswordHistory`).

## 1.8 Unused / safe to remove (not used by any Swagger API)

These are defined in `app/schemas/auth.py` but are never imported or returned by any endpoint:

- `MessageResponse` (`app/schemas/auth.py:137`)
- `AuthResponse` (`app/schemas/auth.py:143`)
- `HospitalAdminResponse` (`app/schemas/auth.py:152`)

Removing them keeps the authentication module limited to what the live APIs actually use.
