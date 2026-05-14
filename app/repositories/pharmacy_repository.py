"""
Pharmacy Repository - Database operations for pharmacy module
"""
from sqlalchemy import select, and_, or_, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from typing import List, Optional
from uuid import UUID
from datetime import date, datetime

from app.models.pharmacy import (
    Medicine, Supplier, PurchaseOrder, PurchaseOrderItem,
    GoodsReceipt, GoodsReceiptItem, StockBatch, Sale, SaleItem, Return, ReturnItem,
    StockLedger, ExpiryAlert
)


class PharmacyRepository:
    """Repository for pharmacy operations"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    # ============================================================================
    # MEDICINE OPERATIONS
    # ============================================================================
    
    async def get_medicine_by_id(self, medicine_id: UUID, hospital_id: UUID) -> Optional[Medicine]:
        """Get medicine by ID"""
        result = await self.db.execute(
            select(Medicine).where(
                and_(
                    Medicine.id == medicine_id,
                    Medicine.hospital_id == hospital_id,
                    Medicine.is_active == True
                )
            )
        )
        return result.scalar_one_or_none()
    
    async def search_medicines(
        self,
        hospital_id: UUID,
        search: Optional[str] = None,
        category: Optional[str] = None,
        status: Optional[str] = None,
        skip: int = 0,
        limit: int = 100
    ) -> List[Medicine]:
        """Search medicines with filters"""
        query = select(Medicine).where(
            and_(
                Medicine.hospital_id == hospital_id,
                Medicine.is_active == True
            )
        )
        
        if search:
            search_filter = or_(
                Medicine.generic_name.ilike(f"%{search}%"),
                Medicine.brand_name.ilike(f"%{search}%"),
                Medicine.manufacturer.ilike(f"%{search}%")
            )
            query = query.where(search_filter)
        
        if category:
            query = query.where(Medicine.category == category)
        
        if status:
            if status.upper() == "ACTIVE":
                query = query.where(Medicine.is_active == True)
            elif status.upper() == "INACTIVE":
                query = query.where(Medicine.is_active == False)
        
        query = query.offset(skip).limit(limit)
        result = await self.db.execute(query)
        return result.scalars().all()
    
    async def create_medicine(self, medicine: Medicine) -> Medicine:
        """Create new medicine"""
        self.db.add(medicine)
        await self.db.flush()
        await self.db.refresh(medicine)
        return medicine
    
    async def update_medicine(self, medicine: Medicine) -> Medicine:
        """Update medicine"""
        await self.db.flush()
        await self.db.refresh(medicine)
        return medicine
    
    # ============================================================================
    # STOCK OPERATIONS
    # ============================================================================
    
    async def get_stock_batches(
        self,
        hospital_id: UUID,
        medicine_id: Optional[UUID] = None,
        skip: int = 0,
        limit: int = 100,
        include_expired: bool = False,
        low_stock: bool = False,
        expiring_in_days: Optional[int] = None
    ) -> List[StockBatch]:
        """Get stock batches"""
        query = select(StockBatch).where(
            and_(
                StockBatch.hospital_id == hospital_id,
                StockBatch.is_active == True
            )
        )
        
        if medicine_id:
            query = query.where(StockBatch.medicine_id == medicine_id)
        
        if not include_expired:
            query = query.where(StockBatch.expiry_date >= date.today())
        
        if expiring_in_days is not None:
            from datetime import timedelta
            threshold = date.today() + timedelta(days=expiring_in_days)
            query = query.where(StockBatch.expiry_date <= threshold)
        
        query = query.order_by(StockBatch.expiry_date).offset(skip).limit(limit)
        result = await self.db.execute(query)
        batches = result.scalars().all()
        
        if low_stock and medicine_id:
            # Filter by reorder level would need Medicine join; for now return as-is
            pass
        return batches
    
    async def get_available_stock(
        self,
        hospital_id: UUID,
        medicine_id: UUID
    ) -> float:
        """Get total available stock for a medicine"""
        result = await self.db.execute(
            select(func.sum(StockBatch.qty_on_hand - StockBatch.qty_reserved)).where(
                and_(
                    StockBatch.hospital_id == hospital_id,
                    StockBatch.medicine_id == medicine_id,
                    StockBatch.is_active == True,
                    StockBatch.expiry_date >= date.today()
                )
            )
        )
        total = result.scalar()
        return float(total) if total else 0.0
    
    # ============================================================================
    # SUPPLIER OPERATIONS
    # ============================================================================
    
    async def get_suppliers(
        self,
        hospital_id: UUID,
        status: Optional[str] = None
    ) -> List[Supplier]:
        """Get suppliers"""
        query = select(Supplier).where(
            and_(
                Supplier.hospital_id == hospital_id,
                Supplier.is_active == True
            )
        )
        
        if status:
            query = query.where(Supplier.status == status)
        
        result = await self.db.execute(query)
        return result.scalars().all()

    async def get_supplier_by_id(self, supplier_id: UUID, hospital_id: UUID) -> Optional[Supplier]:
        """Get supplier by ID"""
        result = await self.db.execute(
            select(Supplier).where(
                and_(
                    Supplier.id == supplier_id,
                    Supplier.hospital_id == hospital_id,
                    Supplier.is_active == True
                )
            )
        )
        return result.scalar_one_or_none()
    
    async def create_supplier(self, supplier: Supplier) -> Supplier:
        """Create new supplier"""
        self.db.add(supplier)
        await self.db.flush()
        await self.db.refresh(supplier)
        return supplier

    # ============================================================================
    # GRN OPERATIONS
    # ============================================================================
    
    async def get_grns(
        self,
        hospital_id: UUID,
        supplier_id: Optional[UUID] = None,
        skip: int = 0,
        limit: int = 100
    ) -> List[GoodsReceipt]:
        """Get GRNs"""
        query = select(GoodsReceipt).where(
            and_(
                GoodsReceipt.hospital_id == hospital_id,
                GoodsReceipt.is_active == True
            )
        )
        
        if supplier_id:
            query = query.where(GoodsReceipt.supplier_id == supplier_id)
        
        query = query.order_by(desc(GoodsReceipt.created_at)).offset(skip).limit(limit)
        result = await self.db.execute(query)
        return result.scalars().all()
    
    async def get_grn_by_id(self, grn_id: UUID, hospital_id: UUID) -> Optional[GoodsReceipt]:
        """Get GRN by ID with items loaded"""
        result = await self.db.execute(
            select(GoodsReceipt)
            .options(selectinload(GoodsReceipt.items))
            .where(
                and_(
                    GoodsReceipt.id == grn_id,
                    GoodsReceipt.hospital_id == hospital_id,
                    GoodsReceipt.is_active == True
                )
            )
        )
        return result.scalar_one_or_none()
    
    async def get_grn_items(self, grn_id: UUID, hospital_id: UUID) -> List[GoodsReceiptItem]:
        """Get GRN items"""
        result = await self.db.execute(
            select(GoodsReceiptItem).where(
                and_(
                    GoodsReceiptItem.grn_id == grn_id,
                    GoodsReceiptItem.hospital_id == hospital_id,
                    GoodsReceiptItem.is_active == True
                )
            )
        )
        return result.scalars().all()

    # ============================================================================
    # PURCHASE ORDER OPERATIONS
    # ============================================================================
    
    async def get_purchase_orders(
        self,
        hospital_id: UUID,
        supplier_id: Optional[UUID] = None,
        status: Optional[str] = None,
        skip: int = 0,
        limit: int = 100
    ) -> List[PurchaseOrder]:
        """Get purchase orders"""
        query = select(PurchaseOrder).where(
            and_(
                PurchaseOrder.hospital_id == hospital_id,
                PurchaseOrder.is_active == True
            )
        )
        
        if supplier_id:
            query = query.where(PurchaseOrder.supplier_id == supplier_id)
        
        if status:
            query = query.where(PurchaseOrder.status == status)
        
        query = query.order_by(desc(PurchaseOrder.created_at)).offset(skip).limit(limit)
        result = await self.db.execute(query)
        return result.scalars().all()
    
    async def get_purchase_order_by_id(self, po_id: UUID, hospital_id: UUID) -> Optional[PurchaseOrder]:
        """Get purchase order by ID with items eagerly loaded (avoids async lazy-load MissingGreenlet)"""
        result = await self.db.execute(
            select(PurchaseOrder)
            .options(selectinload(PurchaseOrder.items))
            .where(
                and_(
                    PurchaseOrder.id == po_id,
                    PurchaseOrder.hospital_id == hospital_id,
                    PurchaseOrder.is_active == True
                )
            )
        )
        return result.scalar_one_or_none()
    
    # ============================================================================
    # SALES OPERATIONS
    # ============================================================================
    
    async def get_sales(
        self,
        hospital_id: UUID,
        patient_id: Optional[UUID] = None,
        status: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        skip: int = 0,
        limit: int = 100
    ) -> List[Sale]:
        """Get sales with items (and item.medicine) eagerly loaded for serialization."""
        query = (
            select(Sale)
            .options(
                selectinload(Sale.items).selectinload(SaleItem.medicine),
                selectinload(Sale.patient),
            )
            .where(
                and_(
                    Sale.hospital_id == hospital_id,
                    Sale.is_active == True
                )
            )
        )
        if patient_id:
            query = query.where(Sale.patient_id == patient_id)
        if status:
            query = query.where(Sale.status == status)
        if from_date:
            query = query.where(Sale.created_at >= from_date)
        if to_date:
            query = query.where(Sale.created_at <= to_date)
        query = query.order_by(desc(Sale.created_at)).offset(skip).limit(limit)
        result = await self.db.execute(query)
        return result.scalars().all()
    
    async def get_sale_by_idempotency_key(
        self, idempotency_key: str, hospital_id: UUID
    ) -> Optional[Sale]:
        """Get sale by idempotency key (for idempotent create)"""
        result = await self.db.execute(
            select(Sale)
            .options(selectinload(Sale.items))
            .where(
                and_(
                    Sale.idempotency_key == idempotency_key,
                    Sale.hospital_id == hospital_id,
                    Sale.is_active == True,
                )
            )
        )
        return result.scalar_one_or_none()

    async def get_sale_by_id(self, sale_id: UUID, hospital_id: UUID) -> Optional[Sale]:
        """Get sale by ID with items and item.medicine eagerly loaded (avoids MissingGreenlet)."""
        result = await self.db.execute(
            select(Sale)
            .options(
                selectinload(Sale.items).selectinload(SaleItem.medicine),
                selectinload(Sale.patient),
            )
            .where(
                and_(
                    Sale.id == sale_id,
                    Sale.hospital_id == hospital_id,
                    Sale.is_active == True
                )
            )
        )
        return result.scalar_one_or_none()
    
    async def get_sale_items(self, sale_id: UUID, hospital_id: UUID) -> List[SaleItem]:
        """Get sale items"""
        result = await self.db.execute(
            select(SaleItem).where(
                and_(
                    SaleItem.sale_id == sale_id,
                    SaleItem.hospital_id == hospital_id,
                    SaleItem.is_active == True
                )
            )
        )
        return result.scalars().all()
    
    async def get_stock_batch_by_id(self, batch_id: UUID, hospital_id: UUID) -> Optional[StockBatch]:
        """Get stock batch by ID"""
        result = await self.db.execute(
            select(StockBatch).where(
                and_(
                    StockBatch.id == batch_id,
                    StockBatch.hospital_id == hospital_id,
                    StockBatch.is_active == True
                )
            )
        )
        return result.scalar_one_or_none()

    async def get_stock_batch_by_batch_key(
        self, hospital_id: UUID, medicine_id: UUID, batch_no: str, expiry_date
    ) -> Optional[StockBatch]:
        """Get stock batch by unique key (hospital, medicine, batch_no, expiry). Used to merge GRN lines."""
        result = await self.db.execute(
            select(StockBatch).where(
                and_(
                    StockBatch.hospital_id == hospital_id,
                    StockBatch.medicine_id == medicine_id,
                    StockBatch.batch_no == batch_no,
                    StockBatch.expiry_date == expiry_date,
                    StockBatch.is_active == True
                )
            )
        )
        return result.scalar_one_or_none()

    async def get_stock_ledger(
        self,
        hospital_id: UUID,
        medicine_id: Optional[UUID] = None,
        txn_type: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        skip: int = 0,
        limit: int = 100
    ) -> List[StockLedger]:
        """Get stock ledger entries"""
        query = select(StockLedger).where(
            and_(
                StockLedger.hospital_id == hospital_id,
                StockLedger.is_active == True
            )
        )
        if medicine_id:
            query = query.where(StockLedger.medicine_id == medicine_id)
        if txn_type:
            query = query.where(StockLedger.txn_type == txn_type)
        if from_date:
            query = query.where(StockLedger.created_at >= from_date)
        if to_date:
            query = query.where(StockLedger.created_at <= to_date)
        query = query.order_by(desc(StockLedger.created_at)).offset(skip).limit(limit)
        result = await self.db.execute(query)
        return result.scalars().all()

    # ============================================================================
    # RETURNS
    # ============================================================================

    async def get_returns(
        self,
        hospital_id: UUID,
        return_type: Optional[str] = None,
        skip: int = 0,
        limit: int = 100
    ) -> List[Return]:
        """Get returns"""
        query = select(Return).options(
            selectinload(Return.items).selectinload(ReturnItem.medicine),
            selectinload(Return.items)
            .selectinload(ReturnItem.batch)
            .selectinload(StockBatch.medicine),
        ).where(
            and_(
                Return.hospital_id == hospital_id,
                Return.is_active == True
            )
        )
        if return_type:
            query = query.where(Return.return_type == return_type)
        query = query.order_by(desc(Return.returned_at)).offset(skip).limit(limit)
        result = await self.db.execute(query)
        return result.scalars().all()

    # ============================================================================
    # EXPIRY ALERTS
    # ============================================================================

    async def get_alerts(
        self,
        hospital_id: UUID,
        alert_type: Optional[str] = None,
        status_filter: Optional[str] = None,
        skip: int = 0,
        limit: int = 100
    ) -> List[ExpiryAlert]:
        """Get expiry/low stock alerts"""
        query = select(ExpiryAlert).where(
            and_(
                ExpiryAlert.hospital_id == hospital_id,
                ExpiryAlert.is_active == True
            )
        )
        if alert_type:
            query = query.where(ExpiryAlert.alert_type == alert_type)
        if status_filter:
            query = query.where(ExpiryAlert.status == status_filter)
        query = query.offset(skip).limit(limit)
        result = await self.db.execute(query)
        return result.scalars().all()

    # ============================================================================
    # REPORTS (plain data for JSON, no ORM lazy load)
    # ============================================================================

    async def report_sales_summary(
        self, hospital_id: UUID, from_date: str, to_date: str, group_by: str = "day"
    ) -> List[dict]:
        """Aggregate completed sales by period. Date strings YYYY-MM-DD."""
        from datetime import datetime as dt, timedelta
        from_date = from_date[:10] if from_date else ""
        to_date = to_date[:10] if to_date else ""
        try:
            start = dt.strptime(from_date, "%Y-%m-%d")
            end = dt.strptime(to_date, "%Y-%m-%d") + timedelta(days=1)
        except ValueError:
            start = dt.min
            end = dt.max
        date_part = func.date_trunc("day", Sale.created_at) if group_by == "day" else func.date_trunc("month", Sale.created_at)
        q = (
            select(
                date_part.label("period"),
                func.count(Sale.id).label("sale_count"),
                func.coalesce(func.sum(Sale.grand_total), 0).label("total_amount"),
            )
            .where(
                and_(
                    Sale.hospital_id == hospital_id,
                    Sale.is_active == True,
                    Sale.status == "PAID",
                    Sale.created_at >= start,
                    Sale.created_at < end,
                )
            )
            .group_by(date_part)
            .order_by(date_part)
        )
        result = await self.db.execute(q)
        rows = result.all()
        return [
            {
                "period": str(r.period)[:10] if r.period else None,
                "sale_count": r.sale_count,
                "total_amount": float(r.total_amount or 0),
            }
            for r in rows
        ]

    async def report_stock_valuation(self, hospital_id: UUID) -> dict:
        """Stock valuation: batch-level qty * purchase_rate."""
        q = (
            select(
                StockBatch.id,
                StockBatch.medicine_id,
                StockBatch.batch_no,
                StockBatch.expiry_date,
                StockBatch.qty_on_hand,
                StockBatch.purchase_rate,
                (StockBatch.qty_on_hand * StockBatch.purchase_rate).label("value"),
            )
            .where(
                and_(
                    StockBatch.hospital_id == hospital_id,
                    StockBatch.is_active == True,
                    StockBatch.qty_on_hand > 0,
                )
            )
        )
        result = await self.db.execute(q)
        rows = result.all()
        total = sum(float(r.value or 0) for r in rows)
        return {
            "total_value": total,
            "items": [
                {
                    "batch_id": str(r.id),
                    "medicine_id": str(r.medicine_id),
                    "batch_no": r.batch_no,
                    "expiry_date": str(r.expiry_date) if r.expiry_date else None,
                    "qty_on_hand": float(r.qty_on_hand),
                    "purchase_rate": float(r.purchase_rate),
                    "value": float(r.value or 0),
                }
                for r in rows
            ],
        }

    async def report_expiry(self, hospital_id: UUID, near_days: int = 90) -> dict:
        """Batches near expiry and expired."""
        from datetime import timedelta
        today = date.today()
        near_end = today + timedelta(days=near_days)
        q_near = (
            select(StockBatch)
            .where(
                and_(
                    StockBatch.hospital_id == hospital_id,
                    StockBatch.is_active == True,
                    StockBatch.expiry_date >= today,
                    StockBatch.expiry_date <= near_end,
                    StockBatch.qty_on_hand > 0,
                )
            )
            .order_by(StockBatch.expiry_date)
        )
        q_expired = (
            select(StockBatch)
            .where(
                and_(
                    StockBatch.hospital_id == hospital_id,
                    StockBatch.is_active == True,
                    StockBatch.expiry_date < today,
                    StockBatch.qty_on_hand > 0,
                )
            )
            .order_by(StockBatch.expiry_date)
        )
        r_near = (await self.db.execute(q_near)).scalars().all()
        r_expired = (await self.db.execute(q_expired)).scalars().all()

        def _row(b):
            return {
                "batch_id": str(b.id),
                "medicine_id": str(b.medicine_id),
                "batch_no": b.batch_no,
                "expiry_date": str(b.expiry_date) if b.expiry_date else None,
                "qty_on_hand": float(b.qty_on_hand),
            }
        return {"near_expiry": [_row(b) for b in r_near], "expired": [_row(b) for b in r_expired]}

    async def report_fast_slow_moving(
        self, hospital_id: UUID, from_date: str, to_date: str
    ) -> dict:
        """Sold qty by medicine from completed sales; classify fast/slow/normal."""
        to_end = to_date + " 23:59:59" if len(to_date) <= 10 else to_date
        q = (
            select(
                SaleItem.medicine_id,
                func.sum(SaleItem.qty).label("total_qty"),
                func.sum(SaleItem.line_total).label("total_revenue"),
            )
            .join(Sale, SaleItem.sale_id == Sale.id)
            .where(
                and_(
                    SaleItem.hospital_id == hospital_id,
                    Sale.hospital_id == hospital_id,
                    Sale.status == "PAID",
                    Sale.is_active == True,
                    SaleItem.is_active == True,
                    Sale.created_at >= from_date,
                    Sale.created_at <= to_end,
                )
            )
            .group_by(SaleItem.medicine_id)
        )
        result = await self.db.execute(q)
        rows = result.all()
        items = []
        for r in rows:
            qty = float(r.total_qty or 0)
            rev = float(r.total_revenue or 0)
            cat = "FAST" if qty >= 100 else ("NORMAL" if qty >= 10 else "SLOW")
            items.append({"medicine_id": str(r.medicine_id), "total_sold": qty, "total_revenue": rev, "movement_category": cat})
        return {"period": f"{from_date} to {to_date}", "items": items}

    async def report_profit_margins(
        self, hospital_id: UUID, from_date: str, to_date: str
    ) -> dict:
        """Profit by medicine: (unit_price - purchase_rate) * qty from completed sale items."""
        to_end = to_date + " 23:59:59" if len(to_date) <= 10 else to_date
        q = (
            select(
                SaleItem.medicine_id,
                func.sum(SaleItem.qty * (SaleItem.unit_price - StockBatch.purchase_rate)).label("profit"),
                func.sum(SaleItem.line_total).label("revenue"),
            )
            .join(Sale, SaleItem.sale_id == Sale.id)
            .join(StockBatch, and_(SaleItem.batch_id == StockBatch.id, StockBatch.hospital_id == hospital_id))
            .where(
                and_(
                    SaleItem.hospital_id == hospital_id,
                    Sale.hospital_id == hospital_id,
                    Sale.status == "PAID",
                    Sale.is_active == True,
                    SaleItem.is_active == True,
                    Sale.created_at >= from_date,
                    Sale.created_at <= to_end,
                )
            )
            .group_by(SaleItem.medicine_id)
        )
        result = await self.db.execute(q)
        rows = result.all()
        margins = [
            {"medicine_id": str(r.medicine_id), "profit": float(r.profit or 0), "revenue": float(r.revenue or 0)}
            for r in rows
        ]
        return {"period": f"{from_date} to {to_date}", "margins": margins}
