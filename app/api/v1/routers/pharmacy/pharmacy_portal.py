"""
Pharmacist portal: dashboard overview, inventory summary, UI settings.

All routes use tenant-routed ``get_db_session`` (sub DB when provisioned).
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db_session
from app.dependencies.auth import require_admin_or_pharmacist
from app.models.user import User
from app.schemas.response import SuccessResponse
from app.services.pharmacy_service import PharmacyService

router = APIRouter(tags=["Pharmacy - Portal"])


class PharmacyGeneralSettings(BaseModel):
    pharmacy_name: str = ""
    pharmacy_address: str = ""
    phone: str = ""
    email: str = ""


class PharmacyNotificationSettings(BaseModel):
    low_stock_alerts: bool = True
    expiry_alerts: bool = True
    purchase_order_updates: bool = True
    sales_reports_email: bool = False


class PharmacySettingsUpdate(BaseModel):
    general: Optional[PharmacyGeneralSettings] = None
    notifications: Optional[PharmacyNotificationSettings] = None


@router.get("/dashboard/overview", response_model=dict)
async def pharmacy_dashboard_overview(
    current_user: User = Depends(require_admin_or_pharmacist()),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """KPI counts for pharmacist dashboard (tenant DB)."""
    svc = PharmacyService(db)
    data = await svc.get_dashboard_overview(current_user.hospital_id)
    return SuccessResponse(success=True, message="OK", data=data).dict()


@router.get("/inventory", response_model=dict)
async def pharmacy_inventory_summary(
    search: Optional[str] = Query(None, description="Search generic/brand/sku"),
    category: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    current_user: User = Depends(require_admin_or_pharmacist()),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Inventory-style list: one row per medicine + aggregated stock (tenant DB)."""
    svc = PharmacyService(db)
    data = await svc.list_inventory_summary(
        current_user.hospital_id,
        search=search,
        category=category,
        skip=skip,
        limit=limit,
    )
    return SuccessResponse(success=True, message="OK", data=data).dict()


@router.get("/settings", response_model=dict)
async def get_pharmacy_settings(
    current_user: User = Depends(require_admin_or_pharmacist()),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Pharmacy UI settings from tenant ``hospitals.settings['pharmacy_ui']``."""
    svc = PharmacyService(db)
    data = await svc.get_pharmacy_ui_settings(current_user.hospital_id)
    return SuccessResponse(success=True, message="OK", data=data).dict()


@router.put("/settings", response_model=dict)
async def update_pharmacy_settings(
    body: PharmacySettingsUpdate,
    current_user: User = Depends(require_admin_or_pharmacist()),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Merge-update pharmacy UI settings on tenant hospital row."""
    svc = PharmacyService(db)
    payload = body.model_dump(exclude_unset=True)
    data = await svc.update_pharmacy_ui_settings(current_user.hospital_id, payload)
    return SuccessResponse(success=True, message="Settings saved", data=data).dict()
