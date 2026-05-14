# Pharmacy Module Flow

This document explains how the pharmacy module works end to end: which API routes are used, how data moves through router, service, repository, and model layers, and where the data is stored and fetched.

All pharmacy API paths are mounted under:

```text
/api/v1/pharmacy
```

## High Level Architecture

The pharmacy module follows this backend flow:

```text
HTTP request
  -> app/api/v1/routers/pharmacy/*.py
  -> app/services/pharmacy_service.py
  -> app/repositories/pharmacy_repository.py
  -> app/models/pharmacy.py SQLAlchemy models
  -> tenant database tables
```

Most pharmacy routes use `get_db_session`, which is tenant-routed. When a hospital has `tenant_database_name`, pharmacy records are stored in that hospital's tenant database. Authentication still resolves users from the platform database, so some pharmacy routes copy the authenticated user or patient rows into the tenant database before writing foreign-key columns like `created_by`, `approved_by`, `returned_by`, or `patient_id`.

## Main Code Locations

| Area | File |
| --- | --- |
| Router registration | `app/api/v1/api.py` |
| Pharmacy routers | `app/api/v1/routers/pharmacy/` |
| Business logic | `app/services/pharmacy_service.py` |
| Database queries | `app/repositories/pharmacy_repository.py` |
| Pharmacy tables | `app/models/pharmacy.py` |
| Request and response schemas | `app/schemas/pharmacy.py` |
| Supplier CRUD schemas | `app/schemas/pharmacy_suppliers_crud.py` |
| Tenant DB routing | `app/database/session.py`, `app/database/routing.py` |

## Database Context

### Tenant Database

The following pharmacy tables are hospital-scoped and are normally read/written in the tenant database:

| Table | Purpose |
| --- | --- |
| `pharmacy_medicines` | Medicine master data |
| `pharmacy_suppliers` | Supplier master data |
| `pharmacy_purchase_orders` | Purchase order header |
| `pharmacy_purchase_order_items` | Purchase order line items |
| `pharmacy_grns` | Goods receipt header |
| `pharmacy_grn_items` | Goods receipt line items |
| `pharmacy_stock_batches` | Actual stock by medicine, batch, expiry, price |
| `pharmacy_stock_ledger` | Stock audit trail for adjustments and returns |
| `pharmacy_sales` | Sale header |
| `pharmacy_sale_items` | Sale line items |
| `pharmacy_returns` | Patient/supplier return header |
| `pharmacy_return_items` | Return line items |
| `pharmacy_expiry_alerts` | Expiry or low-stock alerts |

Because these models inherit `TenantBaseModel`, every row has `hospital_id`.

### Platform/Main Database

Authentication and hospital registry data live in the platform database:

| Table | Why pharmacy may use it |
| --- | --- |
| `users` | Auth resolves the logged-in staff/admin user from platform DB |
| `roles`, `user_roles` | Role checks and role mirroring |
| `patient_profiles` | Pharmacy sales may receive a `patient_ref` that exists only in platform DB |
| `hospitals` | Hospital registry and sometimes UI settings depending on DB routing |

For sales, the code falls back to platform DB for `patient_ref`, then mirrors the patient user and `PatientProfile` into the tenant DB before creating the sale.

## Access Control

Common dependencies:

| Dependency | Used for |
| --- | --- |
| `require_pharmacy_staff()` | Pharmacist, hospital admin, receptionist-style pharmacy access |
| `require_admin_or_pharmacist()` | Medicine, stock, PO, portal read/write |
| `require_hospital_admin()` | PO approval, void sale, expiry scan |
| `require_hospital_context` | Reads `hospital_id` from token/context |

The route gets `current_user.hospital_id` and passes it to service/repository methods. Every query is filtered by this hospital ID.

## 1. Medicine Master Flow

### APIs

```text
GET    /api/v1/pharmacy/medicines
GET    /api/v1/pharmacy/medicines/{medicine_id}
POST   /api/v1/pharmacy/medicines
PUT    /api/v1/pharmacy/medicines/{medicine_id}
DELETE /api/v1/pharmacy/medicines/{medicine_id}
```

### Storage

Main table:

```text
pharmacy_medicines
```

### Flow

1. Client sends medicine details such as generic name, brand name, composition, dosage form, category, reorder level, manufacturer, SKU, and status.
2. Router validates request body using schemas in `app/schemas/pharmacy.py`.
3. Router calls `PharmacyService.create_medicine()` or `update_medicine()`.
4. Service creates/updates a `Medicine` model.
5. Repository writes to `pharmacy_medicines`.
6. Listing/search fetches from `pharmacy_medicines` with filters like search, category, status, skip, and limit.

### Related Fetches

Inventory and reports join medicine data with stock data from `pharmacy_stock_batches`.

## 2. Supplier Flow

### APIs

```text
GET    /api/v1/pharmacy/suppliers
GET    /api/v1/pharmacy/suppliers/{supplier_id}
POST   /api/v1/pharmacy/suppliers
PUT    /api/v1/pharmacy/suppliers/{supplier_id}
DELETE /api/v1/pharmacy/suppliers/{supplier_id}
```

### Storage

Main table:

```text
pharmacy_suppliers
```

### Flow

1. Client creates supplier details.
2. Router in `suppliers.py` validates the request.
3. Service checks uniqueness and business rules.
4. Repository writes to `pharmacy_suppliers`.
5. Purchase orders and GRNs fetch suppliers from `pharmacy_suppliers`.

## 3. Purchase Order Flow

### APIs

```text
GET    /api/v1/pharmacy/purchase-orders
GET    /api/v1/pharmacy/purchase-orders/{po_id}
POST   /api/v1/pharmacy/purchase-orders
PUT    /api/v1/pharmacy/purchase-orders/{po_id}
POST   /api/v1/pharmacy/purchase-orders/{po_id}/submit
POST   /api/v1/pharmacy/purchase-orders/{po_id}/approve
POST   /api/v1/pharmacy/purchase-orders/{po_id}/send
POST   /api/v1/pharmacy/purchase-orders/{po_id}/cancel
DELETE /api/v1/pharmacy/purchase-orders/{po_id}
```

### Storage

Main tables:

```text
pharmacy_purchase_orders
pharmacy_purchase_order_items
```

Related tables:

```text
pharmacy_suppliers
pharmacy_medicines
users
roles
user_roles
```

### Flow

1. Pharmacist or hospital admin creates a purchase order with supplier ID and medicine items.
2. Router calls `ensure_current_user_in_tenant_db()` so `created_by` can reference a user row in tenant `users`.
3. Service generates `po_number`.
4. Service inserts one row in `pharmacy_purchase_orders`.
5. Service inserts rows in `pharmacy_purchase_order_items`.
6. Service calculates subtotal, discount total, tax total, and grand total.
7. Draft PO can be submitted, approved, sent, cancelled, or soft-deleted.
8. Approval writes `approved_by` and `approved_at`.

### Status Flow

```text
DRAFT
  -> PENDING
  -> APPROVED
  -> SENT
  -> PARTIALLY_RECEIVED or RECEIVED
```

The service also allows approval directly from `DRAFT` or `PENDING`.

## 4. GRN Flow

GRN means Goods Receipt Note. This is where received supplier stock becomes actual pharmacy stock.

### APIs

```text
POST /api/v1/pharmacy/grn
GET  /api/v1/pharmacy/grn
GET  /api/v1/pharmacy/grn/{grn_id}
POST /api/v1/pharmacy/grn/{grn_id}/items
POST /api/v1/pharmacy/grn/{grn_id}/finalize
```

### Storage

Main tables:

```text
pharmacy_grns
pharmacy_grn_items
pharmacy_stock_batches
```

Related tables:

```text
pharmacy_suppliers
pharmacy_purchase_orders
pharmacy_purchase_order_items
pharmacy_medicines
```

### Flow

1. User creates GRN for a supplier, optionally linked to a purchase order.
2. Service inserts GRN header into `pharmacy_grns`.
3. User adds received medicine items with batch number, expiry date, received quantity, accepted/rejected quantity, purchase rate, MRP, selling price, tax, and discount.
4. Service inserts each item into `pharmacy_grn_items`.
5. On finalize, service groups GRN items by medicine, batch number, and expiry date.
6. For each group:
   - If matching stock batch exists, increase `qty_on_hand`.
   - If no batch exists, create a new `pharmacy_stock_batches` row.
7. GRN is marked finalized with `finalized_at` and `finalized_by`.

### Important Note

Current finalization updates stock batches. In the current code, GRN finalization does not create `pharmacy_stock_ledger` entries.

## 5. Stock Flow

### APIs

```text
GET  /api/v1/pharmacy/stock
POST /api/v1/pharmacy/stock/adjustments
GET  /api/v1/pharmacy/stock/ledger
```

### Storage

Main tables:

```text
pharmacy_stock_batches
pharmacy_stock_ledger
```

Related table:

```text
pharmacy_medicines
```

### Stock Batch Source

Stock batches are created mainly from finalized GRNs. Each batch stores:

```text
medicine_id
batch_no
expiry_date
purchase_rate
mrp
selling_price
qty_on_hand
qty_reserved
reorder_level
grn_item_id
hospital_id
```

### Stock List Flow

1. Client calls `GET /stock`.
2. Router passes filters such as medicine ID, low stock, expiring days, skip, and limit.
3. Service calls repository `get_stock_batches()`.
4. Repository fetches `pharmacy_stock_batches`.

### Stock Adjustment Flow

1. Client sends `medicine_id`, `batch_id`, `qty_change`, reason, and notes.
2. Service validates medicine and batch.
3. Service updates `pharmacy_stock_batches.qty_on_hand`.
4. Service writes `pharmacy_stock_ledger` with transaction type `ADJUSTMENT`.

### Ledger Fetch Flow

`GET /stock/ledger` reads from `pharmacy_stock_ledger`, optionally filtered by medicine, transaction type, and date range.

## 6. Sales Flow

### APIs

```text
POST /api/v1/pharmacy/sales
GET  /api/v1/pharmacy/sales
GET  /api/v1/pharmacy/sales/{sale_id}
POST /api/v1/pharmacy/sales/{sale_id}/items
POST /api/v1/pharmacy/sales/{sale_id}/complete
POST /api/v1/pharmacy/sales/{sale_id}/void
GET  /api/v1/pharmacy/sales/{sale_id}/receipt
```

### Storage

Main tables:

```text
pharmacy_sales
pharmacy_sale_items
pharmacy_stock_batches
```

Related tables:

```text
patient_profiles
users
roles
user_roles
bills
bill_items
```

### Create Sale Flow

1. Client sends sale type, optional `patient_ref`, billed source, payment method, notes, and items.
2. Router generates or accepts an idempotency key.
3. Router mirrors current pharmacy user into tenant `users` if needed.
4. If `patient_ref` is provided:
   - It first searches tenant `patient_profiles`.
   - If missing, it searches platform `patient_profiles`.
   - If found in platform, it mirrors patient `users` and `patient_profiles` into tenant DB.
5. Service checks if the idempotency key already exists.
6. Service creates `pharmacy_sales` row in `DRAFT` status.
7. For each item:
   - If `batch_id` is omitted, service chooses the first available non-expired batch using FEFO.
   - Service inserts `pharmacy_sale_items`.
8. Sale remains in `DRAFT` until completed.

### Complete Sale Flow

1. Client calls `POST /sales/{sale_id}/complete`.
2. Service fetches sale and sale items.
3. Service validates sale is `DRAFT`.
4. For each sale item:
   - Fetches batch from `pharmacy_stock_batches`.
   - Checks available quantity.
   - Decreases `qty_on_hand`.
5. Sale status is set to `COMPLETED`.
6. If sale has `patient_id`, service attempts billing integration:
   - Finds the patient's open `DRAFT` bill.
   - Adds pharmacy charges into bill items.
   - Recalculates bill totals.
7. If no draft bill exists, sale completion still succeeds and billing logs a warning.

### Important Notes

- Sale completion updates stock batches.
- In the current code, sale completion does not write `pharmacy_stock_ledger` rows.
- Sales list validates response data through `SaleOut`; schema normalizes enum casing for existing DB values.

## 7. Returns Flow

### APIs

```text
POST /api/v1/pharmacy/returns/patient
POST /api/v1/pharmacy/returns/supplier
GET  /api/v1/pharmacy/returns
```

### Storage

Main tables:

```text
pharmacy_returns
pharmacy_return_items
pharmacy_stock_batches
pharmacy_stock_ledger
```

Related tables:

```text
pharmacy_sales
pharmacy_sale_items
pharmacy_suppliers
users
roles
user_roles
```

### Patient Return Flow

Patient return means the patient returns medicines to the pharmacy, so stock increases.

1. Client sends `sale_id`, return reason, and returned items.
2. Router mirrors current user into tenant DB so `returned_by` FK is valid.
3. Service fetches the original sale from `pharmacy_sales`.
4. Service validates each return item against original `pharmacy_sale_items`.
5. Service creates return header in `pharmacy_returns` with `return_type = PATIENT_RETURN`.
6. Service creates line items in `pharmacy_return_items`.
7. Service increases `pharmacy_stock_batches.qty_on_hand`.
8. Service writes `pharmacy_stock_ledger` with `txn_type = RETURN_IN`.

### Supplier Return Flow

Supplier return means pharmacy returns stock to supplier, so stock decreases.

1. Client sends supplier ID, optional GRN ID, return reason, and items.
2. Router mirrors current user into tenant DB.
3. Service validates supplier.
4. Service validates each item has `batch_id`.
5. Service checks available stock in `pharmacy_stock_batches`.
6. Service creates return header in `pharmacy_returns` with `return_type = SUPPLIER_RETURN`.
7. Service creates line items in `pharmacy_return_items`.
8. Service decreases `pharmacy_stock_batches.qty_on_hand`.
9. Service writes `pharmacy_stock_ledger` with `txn_type = RETURN_TO_SUPPLIER_OUT`.

## 8. Alerts Flow

### APIs

```text
GET  /api/v1/pharmacy/alerts
POST /api/v1/pharmacy/alerts/{alert_id}/ack
POST /api/v1/pharmacy/alerts/run-expiry-scan
```

### Storage

Main table:

```text
pharmacy_expiry_alerts
```

Related table:

```text
pharmacy_stock_batches
```

### List/Acknowledge Flow

1. `GET /alerts` reads existing rows from `pharmacy_expiry_alerts`.
2. Each alert row has an `id`; that `id` is the `alert_id`.
3. `POST /alerts/{alert_id}/ack` updates:
   - `status = ACKNOWLEDGED`
   - `acknowledged_by`
   - `acknowledged_at`

### Current Scan Behavior

`run_expiry_scan()` currently returns:

```json
{
  "scanned": 0,
  "alerts_created": 0
}
```

So alerts are only visible if rows already exist in `pharmacy_expiry_alerts`. To make this fully active, `run_expiry_scan()` should be implemented to scan `pharmacy_stock_batches` and create alerts for expired, near-expiry, and low-stock batches.

## 9. Reports Flow

### APIs

```text
GET /api/v1/pharmacy/reports/sales-summary
GET /api/v1/pharmacy/reports/stock-valuation
GET /api/v1/pharmacy/reports/expiry
GET /api/v1/pharmacy/reports/fast-slow-moving
GET /api/v1/pharmacy/reports/profit-margins
```

### Storage/Fetched Tables

| Report | Tables |
| --- | --- |
| Sales summary | `pharmacy_sales` |
| Stock valuation | `pharmacy_stock_batches`, `pharmacy_medicines` |
| Expiry report | `pharmacy_stock_batches` |
| Fast/slow moving | `pharmacy_sales`, `pharmacy_sale_items`, `pharmacy_medicines` |
| Profit margins | `pharmacy_sales`, `pharmacy_sale_items`, `pharmacy_stock_batches`, `pharmacy_medicines` |

Reports are read-only. They aggregate tenant pharmacy data for the current hospital.

## 10. Pharmacy Portal Flow

### APIs

```text
GET /api/v1/pharmacy/dashboard/overview
GET /api/v1/pharmacy/inventory
GET /api/v1/pharmacy/settings
PUT /api/v1/pharmacy/settings
```

### Dashboard Overview

Fetches counts from:

```text
pharmacy_medicines
pharmacy_suppliers
pharmacy_purchase_orders
pharmacy_sales
```

Returns KPIs like:

```text
medicines_count
active_suppliers_count
pending_purchase_orders_count
sales_today_count
```

### Inventory Summary

Fetches from:

```text
pharmacy_medicines
pharmacy_stock_batches
```

Returns one row per medicine with total stock, nearest expiry, unit price, reorder level, and status:

```text
IN_STOCK
LOW_STOCK
OUT_OF_STOCK
```

### Settings

Pharmacy UI settings are stored in the hospital row:

```text
hospitals.settings["pharmacy_ui"]
```

The settings include:

```text
general.pharmacy_name
general.pharmacy_address
general.phone
general.email
notifications.low_stock_alerts
notifications.expiry_alerts
notifications.purchase_order_updates
notifications.sales_reports_email
```

## End-to-End Business Flow

The complete intended pharmacy lifecycle is:

```text
1. Create medicine master
   -> pharmacy_medicines

2. Create supplier
   -> pharmacy_suppliers

3. Create purchase order
   -> pharmacy_purchase_orders
   -> pharmacy_purchase_order_items

4. Submit/approve/send purchase order
   -> pharmacy_purchase_orders.status
   -> approved_by / approved_at when approved

5. Receive stock with GRN
   -> pharmacy_grns
   -> pharmacy_grn_items

6. Finalize GRN
   -> pharmacy_stock_batches created or incremented

7. View stock / inventory
   -> fetch pharmacy_stock_batches
   -> join/aggregate with pharmacy_medicines

8. Create sale
   -> pharmacy_sales
   -> pharmacy_sale_items
   -> optional patient profile mirror from platform DB to tenant DB

9. Complete sale
   -> pharmacy_sales.status = COMPLETED
   -> pharmacy_stock_batches.qty_on_hand decreases
   -> optional bill/bill_items update

10. Handle returns
   -> patient return increases stock
   -> supplier return decreases stock
   -> pharmacy_returns
   -> pharmacy_return_items
   -> pharmacy_stock_ledger

11. Monitor alerts and reports
   -> pharmacy_expiry_alerts
   -> reports aggregate sales, stock, and expiry data
```

## Table Relationship Summary

```text
pharmacy_medicines
  -> pharmacy_purchase_order_items.medicine_id
  -> pharmacy_grn_items.medicine_id
  -> pharmacy_stock_batches.medicine_id
  -> pharmacy_sale_items.medicine_id
  -> pharmacy_return_items.medicine_id
  -> pharmacy_stock_ledger.medicine_id

pharmacy_suppliers
  -> pharmacy_purchase_orders.supplier_id
  -> pharmacy_grns.supplier_id
  -> pharmacy_returns.supplier_id

pharmacy_purchase_orders
  -> pharmacy_purchase_order_items.po_id
  -> pharmacy_grns.po_id

pharmacy_grns
  -> pharmacy_grn_items.grn_id
  -> pharmacy_returns.grn_id

pharmacy_stock_batches
  -> pharmacy_sale_items.batch_id
  -> pharmacy_return_items.batch_id
  -> pharmacy_stock_ledger.batch_id
  -> pharmacy_expiry_alerts.batch_id

pharmacy_sales
  -> pharmacy_sale_items.sale_id
  -> pharmacy_returns.sale_id

pharmacy_returns
  -> pharmacy_return_items.return_id
```

## Common Troubleshooting Notes

### Patient exists in main DB but pharmacy cannot find it

Pharmacy sales first look for `patient_ref` in tenant `patient_profiles`. If missing, the current route falls back to platform `patient_profiles` and mirrors the patient into tenant DB before creating the sale.

### User FK errors on pharmacy writes

Because auth resolves users from platform DB, tenant pharmacy tables may fail FK checks if the same user is not present in tenant `users`. The PO, sales, and returns routers now include tenant-user mirroring before writing fields such as `created_by`, `approved_by`, and `returned_by`.

### Alert scan says zero created alerts

This is expected with the current placeholder implementation of `run_expiry_scan()`. Listing alerts only shows rows already stored in `pharmacy_expiry_alerts`.

### Render still shows old errors

If code is committed and pushed but Render still shows old method errors, redeploy the latest commit or use "Clear build cache & deploy".
