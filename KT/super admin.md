# KT — Super Admin Module

Knowledge-transfer doc for the **Super Admin** endpoints shown in Swagger
(`Super Admin - Hospital Management`, `... Hospital Administrator Management`,
`... Subscription Plan Management`, `... Hospital Subscription Management`,
`... Support Management`, `... Analytics & Monitoring`, `... Notifications`, and the
platform `Analytics` group).

For every endpoint it shows the **full file flow**: which router function receives it,
which service class/method runs the logic, which schema classes validate input/output,
which model classes are touched, and the step-by-step flow.

> Paths are dotted module names. `Class.method` means a method on that class.
> Line numbers are for the current branch and may shift over time.

---

## Endpoints covered

**Super Admin - Hospital Management** (platform-wide hospital CRUD)
1. `GET /api/v1/super-admin/hospitals` — List Hospitals
2. `GET /api/v1/super-admin/hospitals/{hospital_id}` — Get Hospital Details
3. `PUT /api/v1/super-admin/hospitals/{hospital_id}` — Update Hospital
4. `DELETE /api/v1/super-admin/hospitals/{hospital_id}` — Delete Hospital (soft)
5. `PATCH /api/v1/super-admin/hospitals/{hospital_id}/status` — Update Hospital Status
6. `POST /api/v1/super-admin/hospitals/{hospital_id}/deactivate` — Deactivate Hospital (POST alias)

**Super Admin - Hospital Administrator Management**
7. `GET /api/v1/super-admin/hospitals/{hospital_id}/admins` — List Hospital Admins
8. `POST /api/v1/super-admin/hospitals/{hospital_id}/admins` — Create Hospital Admin
9. `PATCH /api/v1/super-admin/hospital-admins/{admin_id}/status` — Update Admin Status
10. `POST /api/v1/super-admin/hospital-admins/{admin_id}/reset-password` — Reset Admin Password

**Super Admin - Subscription Plan Management**
11. `GET /api/v1/super-admin/plans` — List Subscription Plans
12. `POST /api/v1/super-admin/plans` — Create Subscription Plan
13. `PUT /api/v1/super-admin/plans/{plan_id}` — Update Subscription Plan
14. `DELETE /api/v1/super-admin/plans/{plan_id}` — Delete Subscription Plan

**Super Admin - Hospital Subscription Management**
15. `POST /api/v1/super-admin/hospitals/{hospital_name}/assign-plan` — Assign Subscription Plan
16. `GET /api/v1/super-admin/hospitals/{hospital_name}/subscription` — Get Hospital Subscription

**Super Admin - Support Management**
17. `GET /api/v1/super-admin/support/tickets` — List Support Tickets
18. `PATCH /api/v1/super-admin/support/tickets/{ticket_id}/status` — Update Support Ticket Status

**Super Admin - Analytics & Monitoring**
19. `GET /api/v1/super-admin/analytics/overview` — Get Platform Analytics
20. `GET /api/v1/super-admin/dashboard/overview-cards` — Get Dashboard Overview Cards
21. `GET /api/v1/super-admin/subscription-analytics` — Get Subscription Analytics
22. `POST /api/v1/super-admin/subscription-analytics` — Get Subscription Analytics (filtered)
23. `GET /api/v1/super-admin/financial-analytics` — Get Financial Analytics
24. `POST /api/v1/super-admin/financial-analytics` — Get Financial Analytics (filtered)
25. `GET /api/v1/super-admin/performance-analytics` — Get Performance Analytics
26. `GET /api/v1/super-admin/audit-logs` — Get Audit Logs

**Super Admin - Notifications**
27. `POST /api/v1/super-admin/notifications/send-to-hospital-admins` — Notify Hospital Admins

**Analytics** (platform analytics, Super Admin only)
28. `GET /api/v1/analytics/overview` — Get Analytics Overview
29. `GET /api/v1/analytics/reports/system-monitoring` — Reports: System Monitoring
30. `GET /api/v1/analytics/reports/business` — Reports: Business Analytics
31. `GET /api/v1/analytics/audit-logs` — Get Audit Logs

---

## Module file map (what each file is for)

| Layer | Module | File | Role in this module |
| --- | --- | --- | --- |
| Router | `app.api.v1.routers.admin.super_admin` | `app/api/v1/routers/admin/super_admin.py` | Endpoints 1–27 (`/super-admin/*`) |
| Router | `app.api.v1.routers.analytics` | `app/api/v1/routers/analytics.py` | Endpoints 28–31 (`/analytics/*`) |
| Service | `app.services.super_admin_service` | `app/services/super_admin_service.py` | `SuperAdminService` — all hospital/plan/subscription/analytics/support/notify logic |
| Service | `app.services.reports_analytics_service` | `app/services/reports_analytics_service.py` | `ReportsAnalyticsService` — system-monitoring & business reports |
| Service | `app.services.auth_service` | `app/services/auth_service.py` | `AuthService.create_hospital_admin` (used by endpoint 8) |
| Service | `app.services.notifications` | `app/services/notifications/` | `NotificationService` — queues admin emails (endpoint 27) |
| Service | `app.services.email_service` | `app/services/email_service.py` | `EmailService` — ticket status emails (endpoint 18) |
| Schema | `app.schemas.admin` | `app/schemas/admin.py` | Request/response Pydantic models |
| Schema | `app.schemas.response` | `app/schemas/response.py` | `SuccessResponse` envelope |
| RBAC | `app.api.deps` | `app/api/deps.py` | `require_super_admin`, `get_db_session` |
| Model | `app.models.user` | `app/models/user.py` | `User`, `Role`, `AuditLog` |
| Model | `app.models.tenant` | `app/models/tenant.py` | `Hospital`, `SubscriptionPlanModel`, `HospitalSubscription` |
| Model | `app.models.support` | `app/models/support.py` | `SupportTicket` (lives in tenant DBs) |

---

## High-level module flow

```
HTTP request (Super Admin JWT)
   │
   ▼
Router (super_admin.py / analytics.py)
   │  - require_super_admin()  → RBAC gate
   │  - validates body with app.schemas.admin.*
   │  - parses path UUIDs (hospital_id / admin_id / plan_id / ticket_id)
   ▼
SuperAdminService / ReportsAnalyticsService  (get_db_session → platform DB)
   │  - reads/writes Hospital, HospitalSubscription, SubscriptionPlanModel,
   │    User, Role, AuditLog on the PLATFORM DB
   │  - for support tickets, fans out to each hospital's TENANT DB
   │  - _log_admin_action(...) writes AuditLog rows
   ▼
Response (raw dict or app.schemas.response.SuccessResponse)
```

Every endpoint is gated by `require_super_admin()` and runs on the **platform DB** session
(`get_db_session`). The only cross-DB hop is support tickets (endpoints 17–18), which read/write
each hospital's **tenant DB** via `get_tenant_session_factory`.

---

## Endpoint-by-endpoint flow

### Hospital Management

#### 1. `GET /super-admin/hospitals`
- Router: `super_admin.list_hospitals` (`super_admin.py:460`)
- Schema out: `app.schemas.admin.HospitalListOut` (`admin.py:574`)
- Service: `SuperAdminService.get_hospitals` (`super_admin_service.py:76`)
- Flow: paginate + filter (status/subscription/city/state) over `Hospital`, join subscription info.
- Models: `Hospital`, `HospitalSubscription`, `SubscriptionPlanModel`.

#### 2. `GET /super-admin/hospitals/{hospital_id}`
- Router: `super_admin.get_hospital_details` (`super_admin.py:490`) — validates UUID
- Schema out: `app.schemas.admin.HospitalDetailsOut` (`admin.py:580`)
- Service: `SuperAdminService.get_hospital_details` (`super_admin_service.py:176`)
- Flow: load one hospital + subscription + usage metrics + admin contact.
- Models: `Hospital`, `HospitalSubscription`, `User`.

#### 3. `PUT /super-admin/hospitals/{hospital_id}`
- Router: `super_admin.update_hospital` (`super_admin.py:517`)
- Schema in: `app.schemas.admin.HospitalUpdate` (`admin.py:103`)
- Service: `SuperAdminService.update_hospital` (`super_admin_service.py:251`)
- Flow: drop `None` fields → uniqueness checks → update `Hospital` → `_log_admin_action`.
- Models: `Hospital`, `AuditLog`.

#### 4. `DELETE /super-admin/hospitals/{hospital_id}`
- Router: `super_admin.delete_hospital` (`super_admin.py:595`)
- Service: `SuperAdminService.delete_hospital` (`super_admin_service.py:1609`)
- Flow: **soft delete** — set `Hospital.status = INACTIVE`, block tenant users → `SuccessResponse`.
- Models: `Hospital`, `AuditLog`.

#### 5. `PATCH /super-admin/hospitals/{hospital_id}/status`
- Router: `super_admin.update_hospital_status` (`super_admin.py:553`)
- Schema in: `app.schemas.admin.HospitalStatusUpdate` (`admin.py:127`)
- Service: `SuperAdminService.update_hospital_status` (`super_admin_service.py:294`)
- Flow: validate against `HospitalStatus` (ACTIVE/SUSPENDED/INACTIVE) → update `Hospital`.
- Models: `Hospital`, `AuditLog`.

#### 6. `POST /super-admin/hospitals/{hospital_id}/deactivate`
- Router: `super_admin.deactivate_hospital_post` (`super_admin.py:620`)
- Service: `SuperAdminService.delete_hospital` (`super_admin_service.py:1609`) — same as #4.
- Flow: POST alias of DELETE for clients where DELETE is blocked.

### Hospital Administrator Management

#### 7. `GET /super-admin/hospitals/{hospital_id}/admins`
- Router: `super_admin.list_hospital_admins` (`super_admin.py:648`)
- Service: `SuperAdminService.get_hospital_admins` (`super_admin_service.py:359`)
- Flow: list `User`s with HOSPITAL_ADMIN role for the hospital → `{"admins": [...]}`.
- Models: `User`, `Role`.

#### 8. `POST /super-admin/hospitals/{hospital_id}/admins`
- Router: `super_admin.create_hospital_admin` (`super_admin.py:675`)
- Schema in: `app.schemas.admin.HospitalAdminCreate` (`admin.py:188`)
- Service: `AuthService.create_hospital_admin` (delegated, not `SuperAdminService`)
- Flow: create admin `User` (active), assign HOSPITAL_ADMIN `Role`, email-domain validation.
- Models: `User`, `Role`, `Hospital`.

#### 9. `PATCH /super-admin/hospital-admins/{admin_id}/status`
- Router: `super_admin.update_admin_status` (`super_admin.py:709`)
- Schema in: `app.schemas.admin.AdminStatusUpdate` (`admin.py:122`)
- Service: `SuperAdminService.update_admin_status` (`super_admin_service.py:398`)
- Flow: validate against `UserStatus` (ACTIVE/BLOCKED/PENDING) → update admin `User`.
- Models: `User`, `AuditLog`.

#### 10. `POST /super-admin/hospital-admins/{admin_id}/reset-password`
- Router: `super_admin.reset_admin_password` (`super_admin.py:749`)
- Service: `SuperAdminService.reset_admin_password` (`super_admin_service.py:1646`)
- Flow: generate secure temp password → `SecurityManager.hash_password` → update `User`.
- Models: `User`.

### Subscription Plan Management

#### 11. `GET /super-admin/plans`
- Router: `super_admin.list_subscription_plans` (`super_admin.py:798`)
- Service: `SuperAdminService.get_subscription_plans` (`super_admin_service.py:486`)
- Models: `SubscriptionPlanModel`.

#### 12. `POST /super-admin/plans`
- Router: `super_admin.create_subscription_plan` (`super_admin.py:771`)
- Schema in: `app.schemas.admin.SubscriptionPlanCreate` (`admin.py:148`)
- Service: `SuperAdminService.create_subscription_plan` (`super_admin_service.py:447`)
- Flow: validate name against `SubscriptionPlan` (FREE/STANDARD/PREMIUM) → insert plan.
- Models: `SubscriptionPlanModel`.

#### 13. `PUT /super-admin/plans/{plan_id}`
- Router: `super_admin.update_subscription_plan` (`super_admin.py:812`)
- Schema in: `app.schemas.admin.SubscriptionPlanUpdate` (`admin.py:162`)
- Service: `SuperAdminService.update_subscription_plan` (`super_admin_service.py:515`)
- Models: `SubscriptionPlanModel`.

#### 14. `DELETE /super-admin/plans/{plan_id}`
- Router: `super_admin.delete_subscription_plan` (`super_admin.py:845`)
- Service: `SuperAdminService.delete_subscription_plan` (`super_admin_service.py:542`)
- Flow: only deletable when no active subscribers.
- Models: `SubscriptionPlanModel`, `HospitalSubscription`.

### Hospital Subscription Management

#### 15. `POST /super-admin/hospitals/{hospital_name}/assign-plan`
- Router: `super_admin.assign_subscription_plan` (`super_admin.py:872`)
- Schema in: `app.schemas.admin.PlanAssignmentCreate` (`admin.py:175`)
- Service: `SuperAdminService.assign_subscription_plan_by_names` (`super_admin_service.py:577`)
  → `assign_subscription_plan` (`super_admin_service.py:613`)
- Flow: resolve hospital + plan by name → create/update `HospitalSubscription` (upgrade/downgrade).
- Models: `Hospital`, `SubscriptionPlanModel`, `HospitalSubscription`.

#### 16. `GET /super-admin/hospitals/{hospital_name}/subscription`
- Router: `super_admin.get_hospital_subscription` (`super_admin.py:892`)
- Service: `SuperAdminService.get_hospital_subscription_by_name` (`super_admin_service.py:786`)
- Models: `Hospital`, `HospitalSubscription`, `SubscriptionPlanModel`.

### Support Management

#### 17. `GET /super-admin/support/tickets`
- Router: `super_admin.list_support_tickets` (`super_admin.py:915`)
- Service: `SuperAdminService.list_support_tickets` (`super_admin_service.py:1715`)
- Flow: for each hospital with a tenant DB, open a **tenant session** and query `SupportTicket`;
  merge + sort + paginate. Falls back to platform-legacy list if no tenant DBs.
- Models: `SupportTicket` (tenant DB), `Hospital` (platform DB).

#### 18. `PATCH /super-admin/support/tickets/{ticket_id}/status`
- Router: `super_admin.update_support_ticket_status` (`super_admin.py:930`)
- Service: `SuperAdminService.update_support_ticket_status` (`super_admin_service.py:1802`)
- Flow: update ticket status/notes/assignee; on RESOLVED/CLOSED, email the raiser via `EmailService`.
- Models: `SupportTicket` (tenant DB), `User` (platform DB, for email lookup).

### Analytics & Monitoring

#### 19. `GET /super-admin/analytics/overview`
- Router: `super_admin.get_platform_analytics` (`super_admin.py:997`)
- Service: `SuperAdminService.get_platform_analytics` (`super_admin_service.py:852`)
- Flow: KPI cards (appointments, beds, billing, doctors) + subscription breakdown.

#### 20. `GET /super-admin/dashboard/overview-cards`
- Router: `super_admin.get_dashboard_overview_cards` (`super_admin.py:1007`) — `period_days`, `trend_months`
- Service: `SuperAdminService.get_dashboard_overview_cards` (`super_admin_service.py:908`)
- Flow: total hospitals, active paid plans, platform revenue + growth % + trend points.
- Models: `Hospital`, `HospitalSubscription`, `BillingPayment`.

#### 21. `GET /super-admin/subscription-analytics`
- Router: `super_admin.get_subscription_analytics` (`super_admin.py:1045`)
- Service: `SuperAdminService.get_subscription_analytics` (`super_admin_service.py:1144`)

#### 22. `POST /super-admin/subscription-analytics`
- Router: `super_admin.get_subscription_analytics_filtered` (`super_admin.py:1060`)
- Schema in: `AnalyticsFilter` (inline, `super_admin.py:1054`)
- Service: `SuperAdminService.get_subscription_analytics` (`super_admin_service.py:1144`) with date/plan/status filters.

#### 23. `GET /super-admin/financial-analytics`
- Router: `super_admin.get_financial_analytics` (`super_admin.py:1076`)
- Service: `SuperAdminService.get_financial_analytics` (`super_admin_service.py:1396`)

#### 24. `POST /super-admin/financial-analytics`
- Router: `super_admin.get_financial_analytics_filtered` (`super_admin.py:1090`)
- Schema in: `FinancialAnalyticsFilter` (inline, `super_admin.py:1085`)
- Service: `SuperAdminService.get_financial_analytics` (`super_admin_service.py:1396`) with date/hospital filters.

#### 25. `GET /super-admin/performance-analytics`
- Router: `super_admin.get_performance_analytics` (`super_admin.py:1104`)
- Service: `SuperAdminService.get_performance_analytics` (`super_admin_service.py:1571`)

#### 26. `GET /super-admin/audit-logs`
- Router: `super_admin.get_audit_logs` (`super_admin.py:1114`)
- Service: `SuperAdminService.get_platform_audit_logs` (`super_admin_service.py:1669`)
- Models: `AuditLog`.

### Notifications

#### 27. `POST /super-admin/notifications/send-to-hospital-admins`
- Router: `super_admin.notify_hospital_admins` (`super_admin.py:1179`)
- Schema in: `NotifyHospitalAdminsRequest` (inline, `super_admin.py:1131`)
- Service: `SuperAdminService.notify_hospital_admins` (`super_admin_service.py:1872`)
- Flow: resolve target hospital (by `hospital_id`/`hospital_name`, or `notify_all_hospitals`) →
  load HOSPITAL_ADMIN users → `NotificationService.send(channel="EMAIL", ...)` per admin.
- Models: `User`, `Role`, `Hospital`; queues via `NotificationService`.

### Platform Analytics group (`/analytics`)

#### 28. `GET /analytics/overview`
- Router: `analytics.get_analytics_overview` (`analytics.py:15`)
- Service: `SuperAdminService.get_platform_analytics` (`super_admin_service.py:852`) — same as #19.

#### 29. `GET /analytics/reports/system-monitoring`
- Router: `analytics.get_reports_system_monitoring` (`analytics.py:29`) — `days`
- Service: `ReportsAnalyticsService.get_system_monitoring` (`reports_analytics_service.py:26`)
- Flow: active users, activity proxy from `AuditLog`, payment-failure-rate proxy.

#### 30. `GET /analytics/reports/business`
- Router: `analytics.get_reports_business_analytics` (`analytics.py:43`) — `revenue_days`, `hospital_growth_months`
- Service: `ReportsAnalyticsService.get_business_analytics` (`reports_analytics_service.py:103`)
- Flow: revenue trends, hospital growth by month, plan/feature adoption.

#### 31. `GET /analytics/audit-logs`
- Router: `analytics.get_audit_logs` (`analytics.py:63`)
- Service: `SuperAdminService.get_platform_audit_logs` (`super_admin_service.py:1669`) — same as #26.

---

## Shared helpers used across endpoints

- `require_super_admin()` (`app/api/deps.py`) — RBAC gate on every endpoint.
- `get_db_session` / `get_super_admin_service` (`super_admin.py:144`) — platform DB session + service factory.
- `SuperAdminService._get_hospital_by_id` (`:809`), `_verify_super_admin_access` (`:814`),
  `_log_admin_action` (`:823`) — internal helpers (audit + lookups).
- `get_tenant_session_factory` (`app/database/session.py`) — opens per-hospital tenant DB sessions for support tickets.
- `SecurityManager` (`app/core/security.py`) — password hashing for admin reset.

## Notes / overlaps

- Endpoints **19 == 28** and **26 == 31** call the same service methods; the `/analytics/*`
  group is a thin re-exposure of platform analytics under a separate Swagger tag.
- `GET /super-admin/audit-logs` and `GET /analytics/audit-logs` are duplicates of `get_platform_audit_logs`.
- The `super_admin.py` router also defines **Profile Settings** endpoints (`/super-admin/me`,
  `/profile`, `/me/avatar`, `/me/change-password`) that are **not** in the screenshots and are
  therefore not documented here.
