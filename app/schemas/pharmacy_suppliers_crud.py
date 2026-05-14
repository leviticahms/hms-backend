"""
Supplier CRUD bodies aligned with ``app.models.pharmacy.Supplier``.

Kept separate from ``app.schemas.pharmacy`` (nested PO/GRN supplier shapes) to avoid field drift.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class SupplierCreate(BaseModel):
    name: str
    contact_person: Optional[str] = None
    phone: str
    email: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None
    country: Optional[str] = Field(default="India")
    gstin: Optional[str] = None
    drug_license_no: Optional[str] = None
    payment_terms: Optional[str] = "NET_30"
    credit_limit: Optional[float] = None
    rating: Optional[int] = None
    notes: Optional[str] = None


class SupplierUpdate(BaseModel):
    name: Optional[str] = None
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None
    country: Optional[str] = None
    gstin: Optional[str] = None
    drug_license_no: Optional[str] = None
    payment_terms: Optional[str] = None
    credit_limit: Optional[float] = None
    rating: Optional[int] = None
    status: Optional[str] = None
    notes: Optional[str] = None
