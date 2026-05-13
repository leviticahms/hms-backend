"""Stock Management Router - View stock, adjustments, ledger"""
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from uuid import UUID

from app.database.session import get_db_session
from app.dependencies.auth import require_admin_or_pharmacist, require_hospital_context
from app.models.user import User
from app.services.pharmacy_service import PharmacyService
from app.schemas.pharmacy import StockAdjustmentCreate
from app.schemas.response import SuccessResponse

router = APIRouter(prefix="/stock", tags=["Pharmacy - Stock"])


def _batch_to_dict(batch):
    """Serialize StockBatch to dict for JSON (includes batch id for API consumers)."""
    return {
        "id": str(batch.id),
        "medicine_id": str(batch.medicine_id),
        "batch_no": batch.batch_no,
        "expiry_date": batch.expiry_date.isoformat() if hasattr(batch.expiry_date, "isoformat") else str(batch.expiry_date),
        "purchase_rate": float(batch.purchase_rate),
        "mrp": float(batch.mrp),
        "selling_price": float(batch.selling_price),
        "qty_on_hand": float(batch.qty_on_hand),
        "qty_reserved": float(batch.qty_reserved or 0),
        "grn_item_id": str(batch.grn_item_id) if batch.grn_item_id else None,
    }


@router.get("", response_model=dict)
async def list_stock(
    medicine_id: Optional[UUID] = Query(None),
    low_stock: bool = Query(False),
    expiring_in_days: Optional[int] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    current_user: User = Depends(require_admin_or_pharmacist()),
    context: dict = Depends(require_hospital_context),
    db: AsyncSession = Depends(get_db_session)
):
    """
    View stock batches with filters.
    Filters: medicine, low_stock, expiring_in_days.
    Each batch in data.batches has an "id" (batch_id) for use in sales/adjustments.
    """
    service = PharmacyService(db)
    batches = await service.get_stock_batches(
        hospital_id=UUID(context["hospital_id"]),
        medicine_id=medicine_id,
        skip=skip,
        limit=limit,
        low_stock=low_stock,
        expiring_in_days=expiring_in_days
    )
    return SuccessResponse(success=True, message=f"Found {len(batches)} batches", data={"batches": [_batch_to_dict(b) for b in batches]}).dict()


@router.post("/adjustments", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_stock_adjustment(
    adjustment_data: StockAdjustmentCreate,
    current_user: User = Depends(require_admin_or_pharmacist()),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Stock adjustment (increase/decrease with reason).
    Hospital admin or pharmacist. Creates ledger entry.
    """
    service = PharmacyService(db)
    result = await service.create_stock_adjustment(
        hospital_id=current_user.hospital_id,
        performed_by=current_user.id,
        **adjustment_data.dict()
    )
    await db.commit()
    return SuccessResponse(success=True, message="Stock adjusted", data=result).dict()


@router.get("/ledger", response_model=dict)
async def get_stock_ledger(
    medicine_id: Optional[UUID] = Query(None),
    txn_type: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    current_user: User = Depends(require_admin_or_pharmacist()),
    context: dict = Depends(require_hospital_context),
    db: AsyncSession = Depends(get_db_session)
):
    """
    View stock ledger (audit trail).
    Filters: medicine, txn_type, date range
    """
    service = PharmacyService(db)
    entries = await service.get_stock_ledger(
        hospital_id=UUID(context["hospital_id"]),
        medicine_id=medicine_id,
        txn_type=txn_type,
        from_date=from_date,
        to_date=to_date,
        skip=skip,
        limit=limit
    )
    return SuccessResponse(success=True, message=f"Found {len(entries)} entries", data={"ledger": entries}).dict()
