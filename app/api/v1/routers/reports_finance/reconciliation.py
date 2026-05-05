"""
Revenue reconciliation: daily summary, run reconciliation, discrepancy alerts.
RBAC: Hospital Admin, Receptionist.
"""
from uuid import UUID
from datetime import date, datetime
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.session import get_db_session
from app.core.security import get_current_user
from app.api.deps import require_hospital_context, require_roles
from app.core.enums import UserRole
from app.models.user import User
from app.models.billing import Reconciliation, BillingPayment
from app.schemas.billing import (
    ReconciliationRun,
    RunReconciliationBody,
    ReconciliationResponse,
)
from app.schemas.response import SuccessResponse
from app.services.billing.billing_service import BillingService

router = APIRouter(prefix="/finance/reconciliation", tags=["M1.7 Finance - Reconciliation"])
require_billing = require_roles(UserRole.HOSPITAL_ADMIN, UserRole.RECEPTIONIST)


@router.get("/daily", response_model=dict)
async def get_daily_reconciliation(
    date_param: str = Query(..., alias="date", description="YYYY-MM-DD"),
    context: dict = Depends(require_hospital_context),
    user: User = Depends(require_billing),
    db: AsyncSession = Depends(get_db_session),
):
    """Get daily reconciliation summary for a date (payments by method)."""
    hospital_id = UUID(context["hospital_id"])
    d = date.fromisoformat(date_param)
    r = await db.execute(
        select(BillingPayment.method, BillingPayment.amount).where(
            and_(
                BillingPayment.hospital_id == hospital_id,
                BillingPayment.status == "SUCCESS",
                BillingPayment.paid_at >= datetime.combine(d, datetime.min.time()),
                BillingPayment.paid_at <= datetime.combine(d, datetime.max.time()),
            )
        )
    )
    rows = r.all()
    by_method = {}
    for row in rows:
        method = row.method or "OTHER"
        by_method[method] = by_method.get(method, 0) + float(row.amount)
    repo = BillingService(db, hospital_id, UUID(context["user_id"])).repo
    rec = await repo.get_reconciliation_by_date(d)
    data = {
        "date": date_param,
        "by_method": by_method,
        "reconciliation": ReconciliationResponse.model_validate(rec).model_dump() if rec else None,
    }
    return SuccessResponse(success=True, message="Daily summary", data=data).dict()


@router.post("/run", response_model=dict)
async def run_reconciliation(
    body: RunReconciliationBody,
    context: dict = Depends(require_hospital_context),
    user: User = Depends(require_billing),
    db: AsyncSession = Depends(get_db_session),
):
    """Run reconciliation for a date (create reconciliation record)."""
    service = BillingService(db, UUID(context["hospital_id"]), UUID(context["user_id"]))
    d = date.fromisoformat(body.date)
    rec = await service.run_reconciliation(
        recon_date=d,
        total_cash=body.total_cash,
        total_card=body.total_card,
        total_upi=body.total_upi,
        total_online=body.total_online,
        gateway_report_total=body.gateway_report_total,
        notes=body.notes,
    )
    await db.commit()
    await db.refresh(rec)
    return SuccessResponse(success=True, message="Reconciliation run", data=ReconciliationResponse.model_validate(rec).model_dump()).dict()


@router.get("/discrepancies", response_model=dict)
async def list_discrepancies(
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    context: dict = Depends(require_hospital_context),
    user: User = Depends(require_billing),
    db: AsyncSession = Depends(get_db_session),
):
    """List reconciliations with status DISCREPANCY."""
    date_from_d = date.fromisoformat(date_from) if date_from else None
    date_to_d = date.fromisoformat(date_to) if date_to else None
    r = await db.execute(
        select(Reconciliation).where(
            and_(
                Reconciliation.hospital_id == UUID(context["hospital_id"]),
                Reconciliation.status == "DISCREPANCY",
            )
        )
    )
    recs = r.scalars().all()
    if date_from_d:
        recs = [r for r in recs if r.recon_date >= date_from_d]
    if date_to_d:
        recs = [r for r in recs if r.recon_date <= date_to_d]
    data = [ReconciliationResponse.model_validate(r).model_dump() for r in recs]
    return SuccessResponse(success=True, message=f"Found {len(data)} discrepancies", data=data).dict()


@router.get("/{recon_id}", response_model=dict)
async def get_reconciliation(
    recon_id: UUID,
    context: dict = Depends(require_hospital_context),
    user: User = Depends(require_billing),
    db: AsyncSession = Depends(get_db_session),
):
    """Get reconciliation by ID."""
    repo = BillingService(db, UUID(context["hospital_id"]), UUID(context["user_id"])).repo
    rec = await repo.get_reconciliation(recon_id)
    if not rec:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "RECON_NOT_FOUND", "message": "Reconciliation not found"})
    return SuccessResponse(success=True, message="Reconciliation retrieved", data=ReconciliationResponse.model_validate(rec).model_dump()).dict()
