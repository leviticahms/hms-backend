"""
Invoice & receipt generation (PDF + email, templates, duplicate copy).
RBAC: Hospital Admin, Receptionist.
"""
import uuid
import re
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.session import get_db_session
from app.api.deps import require_hospital_context, require_roles
from app.core.enums import UserRole
from app.models.user import User
from app.models.billing import FinancialDocument, Bill, BillingPayment
from app.models.tenant import Hospital
from app.schemas.billing.documents import FinancialDocumentEmailBody
from app.schemas.response import SuccessResponse
from app.services.billing.invoice_pdf import build_invoice_pdf
from app.services.payments.receipt_pdf import build_receipt_pdf
from app.services.email_service import EmailService

router = APIRouter(prefix="/finance/documents", tags=["M1.6 Billing - Invoice & Receipt"])
require_billing = require_roles(UserRole.HOSPITAL_ADMIN, UserRole.RECEPTIONIST)


def _is_valid_email(email: str) -> bool:
    return bool(email and re.match(r"^[^@]+@[^@]+\.[^@]+$", email.strip()))


@router.post("/{doc_id}/email")
async def email_document(
    doc_id: UUID,
    body: FinancialDocumentEmailBody,
    context: dict = Depends(require_hospital_context),
    user: User = Depends(require_billing),
    db: AsyncSession = Depends(get_db_session),
):
    """Email document (invoice/receipt) PDF to recipient. Generates PDF if needed and sends via SMTP."""
    hospital_id = UUID(context["hospital_id"])
    to_email = (body.to_email or "").strip().lower()
    if not _is_valid_email(to_email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_EMAIL", "message": "Valid email address required"},
        )
    r = await db.execute(
        select(FinancialDocument)
        .options(
            selectinload(FinancialDocument.bill).selectinload(Bill.items),
            selectinload(FinancialDocument.bill).selectinload(Bill.patient),
            selectinload(FinancialDocument.payment).selectinload(BillingPayment.bill).selectinload(Bill.patient),
        )
        .where(FinancialDocument.id == doc_id, FinancialDocument.hospital_id == hospital_id)
    )
    doc = r.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "DOC_NOT_FOUND", "message": "Document not found"})
    hr = await db.execute(select(Hospital).where(Hospital.id == hospital_id))
    hospital = hr.scalar_one_or_none()
    doc_type = (doc.doc_type or "").upper()
    pdf_bytes = None
    filename = f"{doc.doc_number or 'document'}.pdf"
    subject = f"{doc_type} {doc.doc_number} - Hospital"
    body_html = f"<p>Please find your {doc_type} attached.</p><p>Document: {doc.doc_number}</p><p>Best regards,<br>Hospital Team</p>"
    if doc_type == "INVOICE" and doc.bill_id and doc.bill:
        bill = doc.bill
        pdf_bytes = build_invoice_pdf(bill=bill, items=getattr(bill, "items", None) or [], patient=getattr(bill, "patient", None), hospital=hospital)
        subject = f"Invoice {bill.bill_number} - Hospital"
    elif doc_type == "RECEIPT" and doc.payment_id and doc.payment:
        pay = doc.payment
        bill = getattr(pay, "bill", None)
        if not bill and doc.bill_id:
            br = await db.execute(select(Bill).options(selectinload(Bill.patient)).where(Bill.id == doc.bill_id, Bill.hospital_id == hospital_id))
            bill = br.scalar_one_or_none()
        payment_like = type("Pay", (), {"amount": getattr(pay, "amount", 0), "currency": "INR", "method": getattr(pay, "method", ""), "provider": getattr(pay, "provider", None), "paid_at": getattr(pay, "paid_at", None), "transaction_id": getattr(pay, "gateway_transaction_id", None), "created_at": getattr(pay, "created_at", None)})()
        pdf_bytes = build_receipt_pdf(payment=payment_like, bill=bill or type("B", (), {"bill_number": doc.doc_number})(), patient=bill.patient if bill and getattr(bill, "patient", None) else None, hospital=hospital, receipt_number=doc.doc_number)
        subject = f"Receipt {doc.doc_number} - Hospital"
    elif doc.bill_id and doc.bill:
        bill = doc.bill
        pdf_bytes = build_invoice_pdf(bill=bill, items=getattr(bill, "items", None) or [], patient=getattr(bill, "patient", None), hospital=hospital)
        subject = f"Document {doc.doc_number} - Hospital"
    if not pdf_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "PDF_UNAVAILABLE", "message": "Cannot generate PDF for this document (missing bill or payment)"},
        )
    email_svc = EmailService()
    await email_svc.send_document_email(to_email=to_email, subject=subject, body_html=body_html, pdf_bytes=pdf_bytes, filename=filename)
    doc.emailed_to = to_email
    await db.commit()
    return SuccessResponse(success=True, message="Document emailed successfully", data={"to_email": to_email, "doc_number": doc.doc_number}).dict()


@router.post("/{doc_id}/duplicate")
async def duplicate_document(
    doc_id: UUID,
    context: dict = Depends(require_hospital_context),
    user: User = Depends(require_billing),
    db: AsyncSession = Depends(get_db_session),
):
    """Create duplicate copy of document (is_duplicate_copy=True)."""
    r = await db.execute(
        select(FinancialDocument).where(
            FinancialDocument.id == doc_id,
            FinancialDocument.hospital_id == UUID(context["hospital_id"]),
        )
    )
    doc = r.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "DOC_NOT_FOUND", "message": "Document not found"})
    dup = FinancialDocument(
        id=uuid.uuid4(),
        hospital_id=doc.hospital_id,
        bill_id=doc.bill_id,
        payment_id=doc.payment_id,
        doc_type=doc.doc_type,
        doc_number=doc.doc_number + "-DUP",
        pdf_path=doc.pdf_path,
        is_duplicate_copy=True,
    )
    db.add(dup)
    await db.flush()
    await db.commit()
    await db.refresh(dup)
    return SuccessResponse(success=True, message="Duplicate copy created", data={"id": str(dup.id), "doc_number": dup.doc_number}).dict()


# SOW: GET /api/v1/finance/templates, PUT /api/v1/finance/templates/{template_id} - stub
@router.get("/templates")
async def list_templates(
    context: dict = Depends(require_hospital_context),
    user: User = Depends(require_billing),
):
    """List document templates. Stub: returns empty list."""
    return SuccessResponse(success=True, message="Templates", data={"templates": []}).dict()


@router.put("/templates/{template_id}")
async def update_template(
    template_id: UUID,
    context: dict = Depends(require_hospital_context),
    user: User = Depends(require_billing),
):
    """Update template. Stub: 501."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail={"code": "TEMPLATES_NOT_IMPLEMENTED", "message": "Template customization not implemented"})
