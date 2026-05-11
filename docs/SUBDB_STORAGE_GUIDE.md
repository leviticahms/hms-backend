# Sub DB (Tenant DB) Storage Guide

This document defines where data must be stored in this project and which files to use when implementing sub-DB behavior.

## Goal

- Each hospital's operational data must be written/read from its own tenant database (sub DB).
- Platform DB is only for global registry/auth/subscription/admin cross-hospital features.

## Data that MUST be in sub DB

Hospital-scoped business tables should live in tenant DB:

- Departments and staffing
  - `departments`
  - `staff_profiles`
  - `staff_department_assignments`
  - `doctor_profiles`
  - `nurse_profiles`
  - `receptionist_profiles`
- Hospital operations
  - `wards`
  - `beds`
  - `admissions`
  - `discharge`-related records
  - `appointments`
  - hospital-scoped patient profile/visit records
- Lab module
  - test registration/catalogue
  - sample tracking
  - equipment and maintenance logs
  - quality control
  - reports/result access tables
- Pharmacy module
  - medicines
  - stock
  - suppliers
  - GRN
  - purchase orders
  - sales
  - returns
  - alerts/reports

## Data that should remain in platform DB

Global/platform tables:

- Hospital registry and tenant mapping
  - `hospitals`
  - `hospitals.tenant_database_name`
- Subscription/billing plan registry
- Platform-level auth resolution (current architecture)
  - `users`
  - `roles`
  - `user_roles`
- Super-admin/global analytics and platform audit logs

## Routing rules (must follow)

1. Use `get_db_session` for hospital-scoped API routes.
2. Avoid `get_platform_db_session` in hospital module CRUD routes.
3. Keep only explicit platform endpoints on platform DB (super-admin/global settings/subscription, etc.).
4. If tenant DB exists (`tenant_database_name` set), the request should route to tenant DB.

## Current project implementation notes

- Hospital admin, lab, and pharmacy APIs are expected to use tenant-routed sessions for business CRUD.
- Platform sync for auth rows may still be used as a mirror path in some flows, but it should not replace tenant as source of truth for hospital business data.

## Verification checklist (manual)

For a test hospital with `tenant_database_name` set:

1. Create department -> row appears in tenant `departments`.
2. Create staff (doctor/nurse/receptionist/lab/pharmacist) -> tenant profile rows appear.
3. Create ward/bed -> rows appear in tenant `wards`/`beds`.
4. Create lab entities -> rows appear in tenant lab tables.
5. Create pharmacy entities -> rows appear in tenant pharmacy tables.
6. Confirm platform DB does not receive business module rows for these entities.

## Files to edit when writing sub-DB code

Use these files as the primary touchpoints:

- Routing and DB session selection
  - `app/database/routing.py`
  - `app/database/session.py`
  - `app/database/tenant_context.py`
- Tenant DB provisioning/schema bootstrap
  - `app/services/tenant_database_provisioning.py`
  - `app/database/schema_patches.py`
- Middleware context
  - `app/middleware/tenant_isolation.py`
- API dependencies
  - `app/api/deps.py`
  - `app/dependencies/auth.py`
- Module routers (must inject `get_db_session` for hospital CRUD)
  - `app/api/v1/routers/admin/hospital_admin.py`
  - `app/api/v1/routers/lab/*.py`
  - `app/api/v1/routers/pharmacy/*.py`
- Service layer (avoid direct platform session for hospital business writes)
  - `app/services/hospital_admin_service.py`
  - `app/services/*lab*.py`
  - `app/services/*pharmacy*.py`

## Pharmacy UI ↔ API map

See **`docs/PHARMACY_SIDEBAR_API_MAP.md`** for pharmacist sidebar items (dashboard, inventory, settings, PO, sales, etc.) and the exact HTTP routes. New portal routes use the same tenant session as the rest of the pharmacy module.

## Coding pattern for sub-DB

Use this pattern for hospital-scoped endpoints:

```python
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db_session

async def create_something(
    db: AsyncSession = Depends(get_db_session),
):
    # db is tenant-routed when tenant_database_name is configured
    ...
```

Use platform session only for global/platform concerns:

```python
from app.core.database import get_platform_db_session
```

