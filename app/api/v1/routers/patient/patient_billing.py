import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_platform_db_session
from app.dependencies.auth import get_current_patient
from app.models.billing.bill import Bill
from app.models.billing.insurance_claim import InsuranceClaim
from app.models.billing.payment import BillingPayment
from app.models.patient import PatientProfile

router = APIRouter(prefix="/patient-billing", tags=["Patient Portal - Billing"])


@router.get("/my/invoices")
async def get_my_invoices(
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session),
):
    result = await db.execute(
        select(Bill)
        .where(Bill.patient_id == current_patient.id)
        .order_by(desc(Bill.created_at))
    )
    bills = result.scalars().all()
    return {
        "invoices": [
            {
                "invoice_id": str(b.id),
                "bill_number": b.bill_number,
                "status": b.status,
                "total_amount": float(b.total_amount or 0),
                "amount_paid": float(b.amount_paid or 0),
                "balance_due": float(b.balance_due or 0),
                "created_at": b.created_at.isoformat() if b.created_at else None,
            }
            for b in bills
        ]
    }


@router.get("/my/invoices/{invoice_id}")
async def get_invoice_details(
    invoice_id: str,
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session),
):
    result = await db.execute(
        select(Bill)
        .where(and_(Bill.id == invoice_id, Bill.patient_id == current_patient.id))
        .options(selectinload(Bill.items), selectinload(Bill.payments))
    )
    bill = result.scalar_one_or_none()
    if not bill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    return {
        "invoice_id": str(bill.id),
        "bill_number": bill.bill_number,
        "status": bill.status,
        "subtotal": float(bill.subtotal or 0),
        "discount_amount": float(bill.discount_amount or 0),
        "tax_amount": float(bill.tax_amount or 0),
        "total_amount": float(bill.total_amount or 0),
        "amount_paid": float(bill.amount_paid or 0),
        "balance_due": float(bill.balance_due or 0),
        "line_items": [
            {
                "id": str(i.id),
                "description": i.description,
                "quantity": float(i.quantity or 0),
                "unit_price": float(i.unit_price or 0),
                "line_total": float(i.line_total or 0),
            }
            for i in bill.items
        ],
    }


@router.post("/payments/process")
async def process_payment(
    payload: Dict[str, Any],
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session),
):
    invoice_id = str(payload.get("invoice_id") or "").strip()
    amount = float(payload.get("amount") or 0)
    method = str(payload.get("method") or "ONLINE_GATEWAY").strip().upper()
    if not invoice_id or amount <= 0:
        raise HTTPException(status_code=400, detail="invoice_id and positive amount are required")
    bill_result = await db.execute(
        select(Bill).where(and_(Bill.id == invoice_id, Bill.patient_id == current_patient.id))
    )
    bill = bill_result.scalar_one_or_none()
    if not bill:
        raise HTTPException(status_code=404, detail="Invoice not found")
    payment = BillingPayment(
        hospital_id=bill.hospital_id,
        bill_id=bill.id,
        payment_ref=f"PATPAY-{uuid.uuid4().hex[:10].upper()}",
        method=method,
        amount=amount,
        status="SUCCESS",
        paid_at=datetime.now(timezone.utc),
        collected_by_user_id=current_patient.user_id,
        provider=str(payload.get("provider") or "manual"),
        gateway_transaction_id=str(payload.get("gateway_transaction_id") or ""),
    )
    bill.amount_paid = float(bill.amount_paid or 0) + amount
    bill.balance_due = max(float(bill.total_amount or 0) - float(bill.amount_paid or 0), 0)
    bill.status = "PAID" if bill.balance_due <= 0 else "PARTIALLY_PAID"
    db.add(payment)
    await db.commit()
    return {"payment_ref": payment.payment_ref, "status": payment.status, "invoice_id": invoice_id}


@router.get("/my/payment-history")
async def get_payment_history(
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session),
):
    result = await db.execute(
        select(BillingPayment)
        .join(Bill, BillingPayment.bill_id == Bill.id)
        .where(Bill.patient_id == current_patient.id)
        .order_by(desc(BillingPayment.created_at))
    )
    rows = result.scalars().all()
    return {
        "payments": [
            {
                "payment_id": str(p.id),
                "payment_ref": p.payment_ref,
                "amount": float(p.amount or 0),
                "status": p.status,
                "method": p.method,
                "paid_at": p.paid_at.isoformat() if p.paid_at else None,
            }
            for p in rows
        ]
    }


@router.get("/my/insurance-details")
async def get_insurance_details(
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session),
):
    claims_result = await db.execute(
        select(InsuranceClaim)
        .where(InsuranceClaim.patient_id == current_patient.id)
        .order_by(desc(InsuranceClaim.created_at))
        .limit(20)
    )
    claims = claims_result.scalars().all()
    return {
        "insurance_provider": current_patient.insurance_provider,
        "policy_number": current_patient.insurance_policy_number,
        "insurance_expiry": current_patient.insurance_expiry,
        "claims": [
            {
                "claim_id": str(c.id),
                "status": c.status,
                "claim_amount": float(c.claim_amount or 0),
                "approved_amount": float(c.approved_amount or 0),
            }
            for c in claims
        ],
    }
