"""Schemas for reconciliation."""
from typing import Optional
from uuid import UUID
from datetime import date, datetime
from decimal import Decimal
from pydantic import BaseModel, Field


class ReconciliationRun(BaseModel):
    date: date


class RunReconciliationBody(BaseModel):
    """POST body when recording a reconciliation row for a date."""

    date: str  # YYYY-MM-DD
    total_cash: float = 0
    total_card: float = 0
    total_upi: float = 0
    total_online: float = 0
    gateway_report_total: float | None = None
    notes: str | None = None


class ReconciliationResponse(BaseModel):
    id: UUID
    hospital_id: UUID
    recon_date: date
    total_cash: Decimal
    total_card: Decimal
    total_upi: Decimal
    total_online: Decimal
    gateway_report_total: Optional[Decimal]
    discrepancy_amount: Optional[Decimal]
    status: str
    notes: Optional[str]
    created_by_user_id: UUID
    created_at: datetime

    class Config:
        from_attributes = True
