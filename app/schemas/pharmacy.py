"""
Pharmacy Management Schemas
Pydantic models for request/response validation for pharmacy operations.
"""
from typing import List, Optional, Dict, Any, Union
from datetime import datetime, date
from decimal import Decimal
from pydantic import BaseModel, Field, validator, field_validator
from uuid import UUID

from app.core.enums import (
    DosageForm, MedicineCategory, MedicineStatus, SupplierStatus,
    PurchaseOrderStatus, SaleType, SaleStatus, PaymentStatus, PaymentMethod,
    StockTransactionType, StockAdjustmentReason
)


# ============================================================================
# MEDICINE SCHEMAS
# ============================================================================

class MedicineBase(BaseModel):
    """Base medicine schema. Coerces DB shape (composition str, pack_size int, category str) when loading from ORM."""
    generic_name: str = Field(..., min_length=1, max_length=255)
    brand_name: str = Field(..., min_length=1, max_length=255)
    composition: List[str] = Field(default_factory=list)
    hsn_code: Optional[str] = Field(None, max_length=20)
    sku: Optional[str] = Field(None, max_length=100)
    barcode: Optional[str] = Field(None, max_length=100)
    dosage_form: DosageForm
    strength: Optional[str] = Field(None, max_length=100)
    manufacturer: Optional[str] = Field(None, max_length=255)
    drug_class: Optional[str] = Field(None, max_length=100)
    route: Optional[str] = Field(None, max_length=50)
    pack_size: Optional[str] = Field(None, max_length=50)
    category: Optional[MedicineCategory] = None
    is_schedule_h: bool = False
    is_narcotic: bool = False
    status: MedicineStatus = MedicineStatus.ACTIVE

    @field_validator("composition", mode="before")
    @classmethod
    def coerce_composition(cls, v: Union[str, List[str], None]) -> List[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [v.strip()] if v.strip() else []
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return []

    @field_validator("pack_size", mode="before")
    @classmethod
    def coerce_pack_size(cls, v: Union[int, str, None]) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, int):
            return str(v)
        return str(v) if v else None

    @field_validator("category", mode="before")
    @classmethod
    def coerce_category(cls, v: Union[str, MedicineCategory, None]) -> Optional[MedicineCategory]:
        if v is None:
            return None
        if isinstance(v, MedicineCategory):
            return v
        s = str(v).strip().upper()
        if not s:
            return None
        if s == "ANALGESIC":
            return MedicineCategory.PAINKILLER
        try:
            return MedicineCategory(s)
        except ValueError:
            return None


class MedicineCreate(MedicineBase):
    """Create medicine request"""
    pass


class MedicineUpdate(BaseModel):
    """Update medicine request"""
    generic_name: Optional[str] = None
    brand_name: Optional[str] = None
    composition: Optional[List[str]] = None
    hsn_code: Optional[str] = None
    sku: Optional[str] = None
    barcode: Optional[str] = None
    dosage_form: Optional[DosageForm] = None
    strength: Optional[str] = None
    manufacturer: Optional[str] = None
    drug_class: Optional[str] = None
    route: Optional[str] = None
    pack_size: Optional[str] = None
    category: Optional[MedicineCategory] = None
    is_schedule_h: Optional[bool] = None
    is_narcotic: Optional[bool] = None
    status: Optional[MedicineStatus] = None


class MedicineOut(MedicineBase):
    """Medicine response"""
    id: UUID
    hospital_id: UUID
    created_at: datetime
    updated_at: datetime
    is_active: bool

    class Config:
        from_attributes = True


class MedicineSearchResult(BaseModel):
    """Medicine search result with stock info"""
    id: UUID
    generic_name: str
    brand_name: str
    dosage_form: str
    strength: Optional[str]
    manufacturer: Optional[str]
    pack_size: Optional[str]
    total_stock: Decimal
    is_available: bool
    selling_price: Optional[Decimal] = None


# ============================================================================
# SUPPLIER SCHEMAS (nested PO/GRN responses — not the supplier CRUD API shape)
# CRUD request bodies live in ``app.schemas.pharmacy_suppliers_crud`` (matches ORM Supplier).
# ============================================================================

class SupplierBase(BaseModel):
    """Base supplier schema"""
    name: str = Field(..., min_length=1, max_length=255)
    phone: str = Field(..., min_length=10, max_length=20)
    email: Optional[str] = Field(None, max_length=255)
    address: Optional[str] = None
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    country: str = Field(default="India", max_length=100)
    pincode: Optional[str] = Field(None, max_length=10)
    gstin: Optional[str] = Field(None, max_length=15)
    pan: Optional[str] = Field(None, max_length=10)
    credit_terms_days: int = Field(default=30, ge=0)
    rating: int = Field(default=5, ge=1, le=5)
    status: SupplierStatus = SupplierStatus.ACTIVE


class SupplierCreate(SupplierBase):
    """Create supplier request"""
    pass


class SupplierUpdate(BaseModel):
    """Update supplier request"""
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    pincode: Optional[str] = None
    gstin: Optional[str] = None
    pan: Optional[str] = None
    credit_terms_days: Optional[int] = None
    rating: Optional[int] = None
    status: Optional[SupplierStatus] = None


class SupplierOut(SupplierBase):
    """Supplier response"""
    id: UUID
    hospital_id: UUID
    created_at: datetime
    updated_at: datetime
    is_active: bool

    class Config:
        from_attributes = True


# ============================================================================
# PURCHASE ORDER SCHEMAS
# ============================================================================

class PurchaseOrderItemCreate(BaseModel):
    """Create purchase order item"""
    medicine_id: UUID
    ordered_qty: Decimal = Field(..., gt=0)
    purchase_rate: Decimal = Field(..., ge=0)
    tax_percent: Decimal = Field(default=0, ge=0, le=100)
    discount_percent: Decimal = Field(default=0, ge=0, le=100)


class PurchaseOrderItemUpdate(BaseModel):
    """Update purchase order item"""
    ordered_qty: Optional[Decimal] = None
    purchase_rate: Optional[Decimal] = None
    tax_percent: Optional[Decimal] = None
    discount_percent: Optional[Decimal] = None


class PurchaseOrderItemOut(BaseModel):
    """Purchase order item response"""
    id: UUID
    medicine_id: UUID
    medicine: Optional[MedicineOut] = None
    ordered_qty: Decimal
    received_qty: Decimal
    purchase_rate: Decimal
    tax_percent: Decimal
    discount_percent: Decimal
    line_total: Decimal

    class Config:
        from_attributes = True


class PurchaseOrderCreate(BaseModel):
    """Create purchase order request"""
    supplier_id: UUID
    expected_date: Optional[date] = None
    notes: Optional[str] = None
    items: List[PurchaseOrderItemCreate] = Field(..., min_length=1)


class PurchaseOrderUpdate(BaseModel):
    """Update purchase order request"""
    expected_date: Optional[date] = None
    notes: Optional[str] = None


class PurchaseOrderOut(BaseModel):
    """Purchase order response"""
    id: UUID
    hospital_id: UUID
    supplier_id: UUID
    supplier: Optional[SupplierOut] = None
    po_number: str
    status: PurchaseOrderStatus
    expected_date: Optional[date]
    approved_at: Optional[datetime]
    sent_at: Optional[datetime]
    subtotal: Decimal
    tax_total: Decimal
    discount_total: Decimal
    grand_total: Decimal
    created_by: UUID
    approved_by: Optional[UUID]
    notes: Optional[str]
    items: List[PurchaseOrderItemOut] = []
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============================================================================
# GRN SCHEMAS
# ============================================================================

class GRNItemCreate(BaseModel):
    """Create GRN item"""
    medicine_id: UUID
    batch_no: str = Field(..., min_length=1, max_length=100)
    expiry_date: date
    received_qty: Decimal = Field(..., gt=0)
    free_qty: Decimal = Field(default=0, ge=0)
    purchase_rate: Decimal = Field(..., ge=0)
    mrp: Decimal = Field(..., ge=0)
    selling_price: Decimal = Field(..., ge=0)
    tax_percent: Decimal = Field(default=0, ge=0, le=100)

    @validator('expiry_date')
    def validate_expiry_date(cls, v):
        if v < date.today():
            raise ValueError('Expiry date cannot be in the past')
        return v


class GRNItemOut(BaseModel):
    """GRN item response"""
    id: UUID
    medicine_id: UUID
    medicine: Optional[MedicineOut] = None
    batch_no: str
    expiry_date: date
    received_qty: Decimal
    free_qty: Decimal
    purchase_rate: Decimal
    mrp: Decimal
    selling_price: Decimal
    tax_percent: Decimal

    class Config:
        from_attributes = True


class GRNCreate(BaseModel):
    """Create GRN request"""
    supplier_id: UUID
    po_id: Optional[UUID] = None
    received_at: datetime = Field(default_factory=datetime.utcnow)
    notes: Optional[str] = None
    items: List[GRNItemCreate] = Field(..., min_length=1)


class GRNOut(BaseModel):
    """GRN response"""
    id: UUID
    hospital_id: UUID
    supplier_id: UUID
    supplier: Optional[SupplierOut] = None
    po_id: Optional[UUID]
    purchase_order: Optional[PurchaseOrderOut] = None
    grn_number: str
    received_at: datetime
    received_by: UUID
    finalized_at: Optional[datetime]
    finalized_by: Optional[UUID]
    is_finalized: bool
    notes: Optional[str]
    items: List[GRNItemOut] = []
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============================================================================
# STOCK SCHEMAS
# ============================================================================

class StockBatchOut(BaseModel):
    """Stock batch response"""
    id: UUID
    medicine_id: UUID
    medicine: Optional[MedicineOut] = None
    batch_no: str
    expiry_date: date
    purchase_rate: Decimal
    mrp: Decimal
    selling_price: Decimal
    qty_on_hand: Decimal
    qty_reserved: Decimal
    reorder_level: Optional[Decimal]
    is_expired: bool
    days_until_expiry: Optional[int]

    class Config:
        from_attributes = True


class StockAdjustmentCreate(BaseModel):
    """Stock adjustment request"""
    medicine_id: UUID
    batch_id: Optional[UUID] = None
    qty_change: Decimal = Field(..., description="Positive for increase, negative for decrease")
    reason: StockAdjustmentReason
    notes: Optional[str] = None


class StockLedgerEntryOut(BaseModel):
    """Stock ledger entry response"""
    id: UUID
    medicine_id: UUID
    medicine: Optional[MedicineOut] = None
    batch_id: Optional[UUID]
    batch: Optional[StockBatchOut] = None
    txn_type: str
    qty_change: Decimal
    unit_cost: Decimal
    reference_type: Optional[str]
    reference_id: Optional[UUID]
    performed_by: UUID
    reason: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


# ============================================================================
# SALES SCHEMAS
# ============================================================================

class SaleItemCreate(BaseModel):
    """Create sale item. Batch is chosen internally (FEFO); not exposed in API."""
    medicine_id: UUID
    qty: Decimal = Field(..., gt=0)
    unit_price: Optional[Decimal] = None  # If None, uses batch selling_price
    discount: Decimal = Field(default=0, ge=0)


class SaleItemOut(BaseModel):
    """Sale item response (batch is internal only, not exposed)."""
    id: UUID
    medicine_id: UUID
    medicine: Optional[MedicineOut] = None
    qty: Decimal
    unit_price: Decimal
    discount: Decimal
    tax: Decimal
    line_total: Decimal

    class Config:
        from_attributes = True


class SaleCreate(BaseModel):
    """Create sale request.
    Use patient_ref (e.g. PAT-001, PID-123) - hospital-specific patient ID from registration.
    """
    sale_type: SaleType
    patient_ref: Optional[str] = None  # PatientProfile.patient_id (e.g. PAT-001)
    prescription_id: Optional[UUID] = None
    billed_via: str = Field(default="PHARMACY_COUNTER", max_length=20)
    payment_method: Optional[PaymentMethod] = None
    notes: Optional[str] = None
    items: List[SaleItemCreate] = Field(..., min_length=1)
    idempotency_key: Optional[str] = Field(None, max_length=100)


class SaleUpdate(BaseModel):
    """Update sale request"""
    notes: Optional[str] = None
    payment_method: Optional[PaymentMethod] = None


class SaleOut(BaseModel):
    """Sale response"""
    id: UUID
    hospital_id: UUID
    sale_number: str
    sale_type: SaleType
    status: SaleStatus
    patient_id: Optional[UUID]
    doctor_id: Optional[UUID]
    prescription_id: Optional[UUID]
    billed_via: str
    billing_invoice_id: Optional[UUID]
    subtotal: Decimal
    tax_total: Decimal
    discount_total: Decimal
    grand_total: Decimal
    payment_status: PaymentStatus
    payment_method: Optional[PaymentMethod]
    paid_amount: Decimal
    created_by: UUID
    completed_at: Optional[datetime]
    voided_at: Optional[datetime]
    voided_by: Optional[UUID]
    void_reason: Optional[str]
    notes: Optional[str]
    items: List[SaleItemOut] = []
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SaleReceiptOut(BaseModel):
    """Sale receipt for printing"""
    sale_number: str
    sale_date: datetime
    patient_name: Optional[str]
    items: List[SaleItemOut]
    subtotal: Decimal
    tax_total: Decimal
    discount_total: Decimal
    grand_total: Decimal
    payment_method: Optional[str]
    payment_status: str


# ============================================================================
# RETURNS SCHEMAS
# ============================================================================

class ReturnItemCreate(BaseModel):
    """Create return item"""
    medicine_id: UUID
    batch_id: Optional[UUID] = None
    qty: Decimal = Field(..., gt=0)
    unit_price: Decimal = Field(..., ge=0)


class PatientReturnCreate(BaseModel):
    """Patient return request"""
    sale_id: UUID
    return_reason: str
    items: List[ReturnItemCreate] = Field(..., min_length=1)


class SupplierReturnCreate(BaseModel):
    """Supplier return request"""
    supplier_id: UUID
    grn_id: Optional[UUID] = None
    return_reason: str
    items: List[ReturnItemCreate] = Field(..., min_length=1)


class ReturnItemOut(BaseModel):
    """Return item response"""
    id: UUID
    medicine_id: UUID
    medicine: Optional[MedicineOut] = None
    batch_id: Optional[UUID]
    batch: Optional[StockBatchOut] = None
    qty: Decimal
    unit_price: Decimal
    line_total: Decimal

    class Config:
        from_attributes = True


class ReturnOut(BaseModel):
    """Return response"""
    id: UUID
    hospital_id: UUID
    return_number: str
    return_type: str
    sale_id: Optional[UUID]
    supplier_id: Optional[UUID]
    grn_id: Optional[UUID]
    patient_id: Optional[UUID]
    total_amount: Decimal
    returned_by: UUID
    return_reason: Optional[str]
    returned_at: datetime
    items: List[ReturnItemOut] = []
    created_at: datetime

    class Config:
        from_attributes = True


# ============================================================================
# ALERTS SCHEMAS
# ============================================================================

class ExpiryAlertOut(BaseModel):
    """Expiry alert response"""
    id: UUID
    hospital_id: UUID
    batch_id: UUID
    batch: Optional[StockBatchOut] = None
    alert_type: str
    threshold_days: Optional[int]
    status: str
    acknowledged_by: Optional[UUID]
    acknowledged_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


# ============================================================================
# REPORTS SCHEMAS
# ============================================================================

class SalesSummaryOut(BaseModel):
    """Sales summary report"""
    period: str  # Day/Month
    total_sales: int
    total_revenue: Decimal
    total_tax: Decimal
    total_discount: Decimal
    net_revenue: Decimal


class StockValuationOut(BaseModel):
    """Stock valuation report"""
    medicine_id: UUID
    medicine: MedicineOut
    total_qty: Decimal
    total_value: Decimal  # Using FIFO or weighted average
    batches: List[StockBatchOut]


class ExpiryReportOut(BaseModel):
    """Expiry report"""
    batch_id: UUID
    batch: StockBatchOut
    days_until_expiry: int
    alert_type: str


class FastSlowMovingOut(BaseModel):
    """Fast/slow moving items report"""
    medicine_id: UUID
    medicine: MedicineOut
    total_sold: Decimal
    total_revenue: Decimal
    movement_category: str  # FAST, SLOW, NORMAL


class ProfitMarginOut(BaseModel):
    """Profit margin report"""
    medicine_id: UUID
    medicine: MedicineOut
    total_sold: Decimal
    total_revenue: Decimal
    total_cost: Decimal
    profit: Decimal
    margin_percent: Decimal




