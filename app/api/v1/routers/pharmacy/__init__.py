"""
Pharmacy Module API Router
Combines all pharmacy sub-routers into single module.
"""
from fastapi import APIRouter

from app.api.v1.routers.pharmacy import (
    medicines,
    suppliers,
    purchase_orders,
    grn,
    stock,
    sales,
    returns,
    alerts,
    reports,
    pharmacy_portal,
)

# Create main pharmacy router
pharmacy_router = APIRouter(prefix="/pharmacy", tags=["Pharmacy"])

# Include all sub-routers
pharmacy_router.include_router(medicines.router)
pharmacy_router.include_router(suppliers.router)
pharmacy_router.include_router(purchase_orders.router)
pharmacy_router.include_router(grn.router)
pharmacy_router.include_router(stock.router)
pharmacy_router.include_router(sales.router)
pharmacy_router.include_router(returns.router)
pharmacy_router.include_router(alerts.router)
pharmacy_router.include_router(reports.router)
pharmacy_router.include_router(pharmacy_portal.router)

__all__ = ["pharmacy_router"]

