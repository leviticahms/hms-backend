"""
Service/Item master and tax profiles - CRUD + department-wise + tax.
RBAC: Hospital Admin, Receptionist (billing).
"""
from uuid import UUID
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db_session
from app.core.security import get_current_user
from app.api.deps import require_hospital_context, require_roles
from app.core.enums import UserRole
from app.models.user import User
from app.schemas.billing import (
    TaxProfileCreate,
    TaxProfileUpdate,
    TaxProfileResponse,
    TaxProfileStatusPatch,
    ServiceItemStatusPatch,
    ServiceItemCreate,
    ServiceItemUpdate,
    ServiceItemResponse,
)
from app.schemas.response import SuccessResponse
from app.services.billing.billing_service import BillingService

router = APIRouter(prefix="/billing", tags=["M1.1 Billing - Tax & Service Master"])

# Billing/cashier: Hospital Admin or Receptionist
require_billing = require_roles(UserRole.HOSPITAL_ADMIN, UserRole.RECEPTIONIST)


@router.post("/tax-profiles", response_model=dict)
async def create_tax_profile(
    body: TaxProfileCreate,
    context: dict = Depends(require_hospital_context),
    user: User = Depends(require_billing),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Create tax profile (e.g. GST 5%).
    
    Access Control:
    - **Who can access:** Hospital Admin, Receptionist
    """
    service = BillingService(db, UUID(context["hospital_id"]), UUID(context["user_id"]))
    t = await service.create_tax_profile(body.name, body.gst_percentage, body.is_active)
    await db.commit()
    return SuccessResponse(success=True, message="Tax profile created", data=TaxProfileResponse.model_validate(t).model_dump()).dict()


@router.get("/tax-profiles", response_model=dict)
async def list_tax_profiles(
    active_only: bool = Query(True),
    context: dict = Depends(require_hospital_context),
    user: User = Depends(require_billing),
    db: AsyncSession = Depends(get_db_session),
):
    """
    List tax profiles.
    
    Access Control:
    - **Who can access:** Hospital Admin, Receptionist
    """
    repo = BillingService(db, UUID(context["hospital_id"]), UUID(context["user_id"])).repo
    items = await repo.list_tax_profiles(active_only=active_only)
    return SuccessResponse(success=True, message=f"Found {len(items)} tax profiles", data=[TaxProfileResponse.model_validate(x).model_dump() for x in items]).dict()


@router.put("/tax-profiles/{tax_id}", response_model=dict)
async def update_tax_profile(
    tax_id: UUID,
    body: TaxProfileUpdate,
    context: dict = Depends(require_hospital_context),
    user: User = Depends(require_billing),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Update tax profile.
    
    Access Control:
    - **Who can access:** Hospital Admin, Receptionist
    """
    service = BillingService(db, UUID(context["hospital_id"]), UUID(context["user_id"]))
    kwargs = body.model_dump(exclude_unset=True)
    t = await service.update_tax_profile(tax_id, **kwargs)
    await db.commit()
    return SuccessResponse(success=True, message="Tax profile updated", data=TaxProfileResponse.model_validate(t).model_dump()).dict()


@router.patch("/tax-profiles/{tax_id}/status", response_model=dict)
async def set_tax_profile_status(
    tax_id: UUID,
    body: TaxProfileStatusPatch,
    context: dict = Depends(require_hospital_context),
    user: User = Depends(require_billing),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Activate/deactivate tax profile.
    
    Access Control:
    - **Who can access:** Hospital Admin, Receptionist
    """
    service = BillingService(db, UUID(context["hospital_id"]), UUID(context["user_id"]))
    t = await service.update_tax_profile(tax_id, is_active=body.is_active)
    await db.commit()
    return SuccessResponse(success=True, message="Tax profile status updated", data=TaxProfileResponse.model_validate(t).model_dump()).dict()


# ---------- Service items ----------
@router.post("/services", response_model=dict)
async def create_service(
    body: ServiceItemCreate,
    context: dict = Depends(require_hospital_context),
    user: User = Depends(require_billing),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Create service/item master.
    
    Access Control:
    - **Who can access:** Hospital Admin, Receptionist
    """
    service = BillingService(db, UUID(context["hospital_id"]), UUID(context["user_id"]))
    try:
        s = await service.create_service_item(
            code=body.code,
            name=body.name,
            category=body.category,
            base_price=body.base_price,
            department_id=body.department_id,
            tax_profile_id=body.tax_profile_id,
            is_active=body.is_active,
        )
    except Exception as e:
        if "DUPLICATE_CODE" in str(e) or "already exists" in str(e).lower():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": "DUPLICATE_CODE", "message": str(e)})
        raise
    await db.commit()
    return SuccessResponse(success=True, message="Service created", data=ServiceItemResponse.model_validate(s).model_dump()).dict()


@router.get("/services", response_model=dict)
async def list_services(
    department_id: UUID | None = Query(None, description="Department UUID"),
    department_name: str | None = Query(None, description="Department name or code (e.g. GEN, Cardiology)"),
    category: str | None = Query(None),
    active: bool = Query(True),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    context: dict = Depends(require_hospital_context),
    user: User = Depends(require_billing),
    db: AsyncSession = Depends(get_db_session),
):
    """List service items with filters. Use department_name (e.g. GEN) or department_id."""
    hospital_id = UUID(context["hospital_id"])
    resolved_dept_id = department_id
    if not resolved_dept_id and department_name:
        from app.models.hospital import Department
        from sqlalchemy import or_
        r = await db.execute(
            select(Department.id).where(
                Department.hospital_id == hospital_id,
                or_(
                    Department.name.ilike(f"%{department_name}%"),
                    Department.code.ilike(f"%{department_name}%"),
                ),
            ).limit(1)
        )
        resolved_dept_id = r.scalar_one_or_none()
    repo = BillingService(db, hospital_id, UUID(context["user_id"])).repo
    items = await repo.list_service_items(department_id=resolved_dept_id, category=category, active_only=active, skip=skip, limit=limit)
    return SuccessResponse(success=True, message=f"Found {len(items)} services", data=[ServiceItemResponse.model_validate(x).model_dump() for x in items]).dict()


@router.get("/services/{service_id}", response_model=dict)
async def get_service(
    service_id: UUID,
    context: dict = Depends(require_hospital_context),
    user: User = Depends(require_billing),
    db: AsyncSession = Depends(get_db_session),
):
    """Get service by ID."""
    repo = BillingService(db, UUID(context["hospital_id"]), UUID(context["user_id"])).repo
    s = await repo.get_service_item(service_id)
    if not s:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "SERVICE_NOT_FOUND", "message": "Service not found"})
    return SuccessResponse(success=True, message="Service retrieved", data=ServiceItemResponse.model_validate(s).model_dump()).dict()


@router.put("/services/{service_id}", response_model=dict)
async def update_service(
    service_id: UUID,
    body: ServiceItemUpdate,
    context: dict = Depends(require_hospital_context),
    user: User = Depends(require_billing),
    db: AsyncSession = Depends(get_db_session),
):
    """Update service item."""
    service = BillingService(db, UUID(context["hospital_id"]), UUID(context["user_id"]))
    kwargs = body.model_dump(exclude_unset=True)
    s = await service.update_service_item(service_id, **kwargs)
    await db.commit()
    return SuccessResponse(success=True, message="Service updated", data=ServiceItemResponse.model_validate(s).model_dump()).dict()


@router.patch("/services/{service_id}/status", response_model=dict)
async def set_service_status(
    service_id: UUID,
    body: ServiceItemStatusPatch,
    context: dict = Depends(require_hospital_context),
    user: User = Depends(require_billing),
    db: AsyncSession = Depends(get_db_session),
):
    """Activate/deactivate service (soft delete)."""
    service = BillingService(db, UUID(context["hospital_id"]), UUID(context["user_id"]))
    s = await service.update_service_item(service_id, is_active=body.is_active)
    await db.commit()
    return SuccessResponse(success=True, message="Service status updated", data=ServiceItemResponse.model_validate(s).model_dump()).dict()


@router.delete("/services/{service_id}", response_model=dict)
async def delete_service(
    service_id: UUID,
    context: dict = Depends(require_hospital_context),
    user: User = Depends(require_billing),
    db: AsyncSession = Depends(get_db_session),
):
    """Soft delete service (set is_active=False)."""
    service = BillingService(db, UUID(context["hospital_id"]), UUID(context["user_id"]))
    await service.update_service_item(service_id, is_active=False)
    await db.commit()
    return SuccessResponse(success=True, message="Service deactivated").dict()
