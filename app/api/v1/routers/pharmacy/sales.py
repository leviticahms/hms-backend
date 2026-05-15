"""Sales/Dispensing Router - Prescription & OTC sales with FEFO"""
from fastapi import APIRouter, Depends, Query, Header, status
from uuid import uuid4
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from uuid import UUID

from app.core.database import get_platform_db_session
from app.database.session import get_db_session
from app.dependencies.auth import require_pharmacy_staff, require_hospital_admin, require_hospital_context
from app.models.user import User
from app.services.patient_tenant_bridge import (
    resolve_patient_profile_id_for_tenant,
    upsert_tenant_user_from_platform_user,
)
from app.services.pharmacy_service import PharmacyService
from app.schemas.pharmacy import SaleCreate, SaleItemCreate, SaleOut
from app.schemas.response import SuccessResponse

router = APIRouter(prefix="/sales", tags=["Pharmacy - Sales"])


@router.post("", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_sale(
    sale_data: SaleCreate,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    current_user: User = Depends(require_pharmacy_staff()),
    db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """Create sale (DRAFT status). Idempotency-Key header optional - auto-generated if omitted.
    Use patient_ref (e.g. PAT-001) - hospital-specific patient ID.
    Same key = returns existing sale (idempotent).
    """
    # Auto-generate idempotency key if not provided (so request always works)
    key = idempotency_key or str(uuid4())
    await upsert_tenant_user_from_platform_user(db, current_user)
    service = PharmacyService(db)
    sale_data_dict = sale_data.model_dump()
    sale_data_dict.pop("idempotency_key", None)  # Use header value, avoid duplicate
    patient_ref = sale_data_dict.pop("patient_ref", None)
    # Resolve patient_ref to patient_id (PatientProfile.id) if provided
    if patient_ref:
        sale_data_dict["patient_id"] = await resolve_patient_profile_id_for_tenant(
            patient_ref,
            current_user.hospital_id,
            db,
            platform_db,
        )
    sale = await service.create_sale(
        hospital_id=current_user.hospital_id,
        created_by=current_user.id,
        idempotency_key=key,
        **sale_data_dict
    )
    await db.commit()
    return SuccessResponse(success=True, message="Sale created", data={"sale_id": str(sale.id), "sale_number": sale.sale_number}).dict()


@router.get("", response_model=dict)
async def list_sales(
    patient_id: Optional[UUID] = Query(None, description="Patient profile UUID"),
    patient_ref: Optional[str] = Query(None, description="Patient reference (e.g. PAT-001)"),
    status_filter: Optional[str] = Query(None, alias="status"),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    current_user: User = Depends(require_pharmacy_staff()),
    context: dict = Depends(require_hospital_context),
    db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """List sales with filters. Use patient_ref (e.g. PAT-001) or patient_id."""
    service = PharmacyService(db)
    hospital_id = UUID(context["hospital_id"])
    resolved_patient_id = patient_id
    if not resolved_patient_id and patient_ref:
        resolved_patient_id = await resolve_patient_profile_id_for_tenant(
            patient_ref, hospital_id, db, platform_db
        )
    sales = await service.get_sales(hospital_id, resolved_patient_id, status_filter, from_date, to_date, skip, limit)
    sales_data = []
    for s in sales:
        d = SaleOut.model_validate(s).model_dump(mode="json")
        d["patient_ref"] = s.patient.patient_id if (s.patient_id and hasattr(s, "patient") and s.patient) else None
        sales_data.append(d)
    return SuccessResponse(success=True, message=f"Found {len(sales)} sales", data={"sales": sales_data}).dict()


@router.get("/{sale_id}", response_model=dict)
async def get_sale(
    sale_id: UUID,
    current_user: User = Depends(require_pharmacy_staff()),
    context: dict = Depends(require_hospital_context),
    db: AsyncSession = Depends(get_db_session)
):
    """Get sale details (batch_id internal only, not in response)."""
    service = PharmacyService(db)
    sale = await service.get_sale(sale_id, UUID(context["hospital_id"]))
    sale_data = SaleOut.model_validate(sale).model_dump(mode="json")
    sale_data["patient_ref"] = sale.patient.patient_id if (sale.patient_id and hasattr(sale, "patient") and sale.patient) else None
    return SuccessResponse(success=True, message="Sale retrieved", data={"sale": sale_data}).dict()


@router.post("/{sale_id}/items", response_model=dict, status_code=status.HTTP_201_CREATED)
async def add_sale_item(
    sale_id: UUID,
    item_data: SaleItemCreate,
    current_user: User = Depends(require_pharmacy_staff()),
    db: AsyncSession = Depends(get_db_session)
):
    """Add item to sale"""
    service = PharmacyService(db)
    item = await service.add_sale_item(sale_id, current_user.hospital_id, **item_data.model_dump())
    await db.commit()
    return SuccessResponse(success=True, message="Item added", data={"item_id": str(item.id)}).dict()


@router.post("/{sale_id}/complete", response_model=dict)
async def complete_sale(
    sale_id: UUID,
    current_user: User = Depends(require_pharmacy_staff()),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Complete sale - deducts stock atomically using FEFO.
    CRITICAL: Uses SELECT FOR UPDATE for concurrency safety.
    """
    await upsert_tenant_user_from_platform_user(db, current_user)
    service = PharmacyService(db)
    result = await service.complete_sale(sale_id, current_user.hospital_id, current_user.id)
    await db.commit()
    return SuccessResponse(success=True, message="Sale completed", data=result).dict()


@router.post("/{sale_id}/void", response_model=dict)
async def void_sale(
    sale_id: UUID,
    reason: str,
    current_user: User = Depends(require_hospital_admin()),
    db: AsyncSession = Depends(get_db_session)
):
    """Void completed sale. Admin only"""
    await upsert_tenant_user_from_platform_user(db, current_user)
    service = PharmacyService(db)
    sale = await service.void_sale(sale_id, current_user.hospital_id, current_user.id, reason)
    await db.commit()
    return SuccessResponse(success=True, message="Sale voided", data={"sale_id": str(sale.id)}).dict()


@router.get("/{sale_id}/receipt", response_model=dict)
async def get_sale_receipt(
    sale_id: UUID,
    current_user: User = Depends(require_pharmacy_staff()),
    context: dict = Depends(require_hospital_context),
    db: AsyncSession = Depends(get_db_session)
):
    """Get printable receipt"""
    service = PharmacyService(db)
    receipt = await service.get_sale_receipt(sale_id, UUID(context["hospital_id"]))
    return SuccessResponse(success=True, message="Receipt generated", data={"receipt": receipt}).dict()

