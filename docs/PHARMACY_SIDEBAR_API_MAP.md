# Pharmacist sidebar → API (sub DB)

All paths below are under **`/api/v1/pharmacy`** and use **`get_db_session`** (tenant / sub DB when `tenant_database_name` is set).

| Sidebar (UI) | Backend | Primary endpoints |
|----------------|---------|---------------------|
| Dashboard Overview | **GET** `.../dashboard/overview` | `app/api/v1/routers/pharmacy/pharmacy_portal.py` |
| Inventory Management | **GET** `.../inventory` (aggregated stock per medicine) | `pharmacy_portal.py` + `PharmacyService.list_inventory_summary` |
| | Also **GET** `.../medicines`, **GET** `.../stock` | `medicines.py`, `stock.py` |
| Purchase Orders | **GET/POST** `.../purchase-orders` | `purchase_orders.py` |
| Sales Tracking | **GET/POST** `.../sales` | `sales.py` |
| Expiry Alerts | **GET/POST** `.../alerts` | `alerts.py` |
| Supplier Management | **GET/POST** `.../suppliers` | `suppliers.py` |
| Medicine Database | **GET/POST** `.../medicines` | `medicines.py` |
| stock | **GET/POST** `.../stock` | `stock.py` |
| return | **POST** `.../returns/patient`, `.../returns/supplier` | `returns.py` |
| GRN | **POST** `.../grn` | `grn.py` |
| report | **GET** `.../reports/*` | `reports.py` |
| Settings | **GET/PUT** `.../settings` | `pharmacy_portal.py` (stored in tenant `hospitals.settings['pharmacy_ui']`) |

## Settings storage

- Tenant row: `hospitals` (same hospital as JWT `hospital_id`).
- JSON key: `settings.pharmacy_ui` with shape:
  - `general`: `pharmacy_name`, `pharmacy_address`, `phone`, `email`
  - `notifications`: `low_stock_alerts`, `expiry_alerts`, `purchase_order_updates`, `sales_reports_email`

## Inventory row shape (`GET /inventory`)

Each item includes: `medicine_id`, display fields, `stock_units`, `unit_price`, `nearest_expiry`, `status` (`IN_STOCK` | `LOW_STOCK` | `OUT_OF_STOCK`).

## Related code

- Service: `app/services/pharmacy_service.py`
- Repository: `app/repositories/pharmacy_repository.py`
- Models: `app/models/pharmacy.py`, `app/models/tenant.py` (`Hospital.settings`)
