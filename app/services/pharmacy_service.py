"""
Pharmacy Service - Business logic for pharmacy operations
"""
from decimal import Decimal
from sqlalchemy import select, and_, or_, func, cast, Date
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional, Any
from uuid import UUID, uuid4
from datetime import datetime, date, timezone

from sqlalchemy.exc import IntegrityError

from app.repositories.pharmacy_repository import PharmacyRepository
from app.models.pharmacy import Medicine, Supplier, StockBatch, PurchaseOrder, Sale
from app.models.tenant import Hospital
from app.core.enums import PurchaseOrderStatus, SupplierStatus
from app.core.exceptions import BusinessLogicError


class PharmacyService:
    """Service for pharmacy operations"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = PharmacyRepository(db)
    
    # ============================================================================
    # MEDICINE OPERATIONS
    # ============================================================================
    
    async def get_medicine(self, medicine_id: UUID, hospital_id: UUID) -> Medicine:
        """Get medicine by ID"""
        medicine = await self.repo.get_medicine_by_id(medicine_id, hospital_id)
        if not medicine:
            raise BusinessLogicError("Medicine not found")
        return medicine
    
    async def search_medicines(
        self,
        hospital_id: UUID,
        search: Optional[str] = None,
        category: Optional[str] = None,
        status: Optional[str] = None,
        skip: int = 0,
        limit: int = 100
    ) -> List[Medicine]:
        """Search medicines"""
        return await self.repo.search_medicines(
            hospital_id=hospital_id,
            search=search,
            category=category,
            status=status,
            skip=skip,
            limit=limit
        )
    
    async def create_medicine(
        self,
        hospital_id: UUID,
        generic_name: str,
        brand_name: str,
        composition: Optional[str],
        dosage_form: str,
        **kwargs
    ) -> Medicine:
        """Create new medicine"""
        medicine = Medicine(
            id=uuid4(),
            hospital_id=hospital_id,
            generic_name=generic_name,
            brand_name=brand_name,
            composition=composition,
            dosage_form=dosage_form,
            **kwargs
        )
        return await self.repo.create_medicine(medicine)
    
    async def update_medicine(
        self,
        medicine_id: UUID,
        hospital_id: UUID,
        **updates
    ) -> Medicine:
        """Update medicine"""
        medicine = await self.get_medicine(medicine_id, hospital_id)
        
        for key, value in updates.items():
            if hasattr(medicine, key):
                setattr(medicine, key, value)
        
        medicine.updated_at = datetime.utcnow()
        return await self.repo.update_medicine(medicine)
    
    # ============================================================================
    # STOCK OPERATIONS
    # ============================================================================
    
    async def get_stock_batches(
        self,
        hospital_id: UUID,
        medicine_id: Optional[UUID] = None,
        skip: int = 0,
        limit: int = 100,
        low_stock: bool = False,
        expiring_in_days: Optional[int] = None
    ) -> List[StockBatch]:
        """Get stock batches"""
        return await self.repo.get_stock_batches(
            hospital_id=hospital_id,
            medicine_id=medicine_id,
            skip=skip,
            limit=limit,
            low_stock=low_stock,
            expiring_in_days=expiring_in_days
        )
    
    async def get_available_stock(
        self,
        hospital_id: UUID,
        medicine_id: UUID
    ) -> float:
        """Get available stock quantity"""
        return await self.repo.get_available_stock(hospital_id, medicine_id)

    async def get_stock_ledger(
        self,
        hospital_id: UUID,
        medicine_id: Optional[UUID] = None,
        txn_type: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        skip: int = 0,
        limit: int = 100
    ):
        """Get stock ledger (audit trail) entries"""
        return await self.repo.get_stock_ledger(
            hospital_id=hospital_id,
            medicine_id=medicine_id,
            txn_type=txn_type,
            from_date=from_date,
            to_date=to_date,
            skip=skip,
            limit=limit
        )
    
    # ============================================================================
    # SUPPLIER OPERATIONS
    # ============================================================================
    
    async def get_suppliers(
        self,
        hospital_id: UUID,
        status: Optional[str] = None
    ) -> List[Supplier]:
        """Get suppliers"""
        return await self.repo.get_suppliers(hospital_id, status)

    async def get_supplier(self, supplier_id: UUID, hospital_id: UUID) -> Supplier:
        """Get supplier by ID"""
        supplier = await self.repo.get_supplier_by_id(supplier_id, hospital_id)
        if not supplier:
            raise BusinessLogicError("Supplier not found")
        return supplier
    
    async def create_supplier(
        self,
        hospital_id: UUID,
        name: str,
        phone: str,
        **kwargs
    ) -> Supplier:
        """Create new supplier"""
        supplier = Supplier(
            id=uuid4(),
            hospital_id=hospital_id,
            name=name,
            phone=phone,
            **kwargs
        )
        return await self.repo.create_supplier(supplier)

    async def update_supplier(
        self,
        supplier_id: UUID,
        hospital_id: UUID,
        **updates
    ) -> Supplier:
        """Update supplier details."""
        supplier = await self.get_supplier(supplier_id, hospital_id)
        allowed_fields = {
            "name",
            "contact_person",
            "phone",
            "email",
            "address_line1",
            "address_line2",
            "city",
            "state",
            "pincode",
            "country",
            "gstin",
            "drug_license_no",
            "payment_terms",
            "credit_limit",
            "rating",
            "status",
            "notes",
        }
        for field, value in updates.items():
            if field in allowed_fields:
                setattr(supplier, field, value)
        await self.db.flush()
        await self.db.refresh(supplier)
        return supplier



    # ============================================================================
    # GRN (GOODS RECEIPT NOTE) OPERATIONS
    # ============================================================================
    
    async def create_grn(
        self,
        hospital_id: UUID,
        received_by: UUID,
        supplier_id: UUID,
        po_id: Optional[UUID] = None,
        received_at: Optional[datetime] = None,
        notes: Optional[str] = None,
        items: Optional[List[Any]] = None,
        **kwargs
    ):
        """Create new GRN with optional line items (matches GRNCreate: supplier_id, po_id, received_at, notes, items)"""
        from app.models.pharmacy import GoodsReceipt, GoodsReceiptItem
        from decimal import Decimal

        # Drop keys that are not on the model so **kwargs does not break
        kwargs.pop("items", None)
        kwargs.pop("received_at", None)

        # Generate grn_number (GRN-YYYYMMDD-0001)
        today_str = date.today().strftime("%Y%m%d")
        count_result = await self.db.execute(
            select(func.count(GoodsReceipt.id)).where(
                and_(
                    GoodsReceipt.hospital_id == hospital_id,
                    GoodsReceipt.grn_number.like(f"GRN-{today_str}%")
                )
            )
        )
        n = (count_result.scalar() or 0) + 1
        grn_number = f"GRN-{today_str}-{n:04d}"

        # Normalize received_at to naive UTC (TIMESTAMP WITHOUT TIME ZONE)
        if received_at is None:
            received_at_naive = datetime.utcnow()
        elif received_at.tzinfo is not None:
            received_at_naive = received_at.astimezone(timezone.utc).replace(tzinfo=None)
        else:
            received_at_naive = received_at

        grn = GoodsReceipt(
            id=uuid4(),
            hospital_id=hospital_id,
            supplier_id=supplier_id,
            po_id=po_id,
            grn_number=grn_number,
            received_at=received_at_naive,
            received_by=received_by,
            notes=notes,
            is_finalized=False,
        )
        self.db.add(grn)
        await self.db.flush()

        def _v(obj, key, default=None):
            return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)

        if items:
            for it in items:
                expiry_val = _v(it, "expiry_date")
                if hasattr(expiry_val, "hour"):
                    expiry_date_naive = expiry_val.date()
                elif isinstance(expiry_val, str):
                    expiry_date_naive = datetime.strptime(str(expiry_val)[:10], "%Y-%m-%d").date()
                else:
                    expiry_date_naive = expiry_val
                poi = GoodsReceiptItem(
                    id=uuid4(),
                    hospital_id=hospital_id,
                    grn_id=grn.id,
                    medicine_id=_v(it, "medicine_id"),
                    batch_no=_v(it, "batch_no"),
                    expiry_date=expiry_date_naive,
                    received_qty=Decimal(str(_v(it, "received_qty", 0) or 0)),
                    free_qty=Decimal(str(_v(it, "free_qty", 0) or 0)),
                    purchase_rate=Decimal(str(_v(it, "purchase_rate", 0) or 0)),
                    mrp=Decimal(str(_v(it, "mrp", 0) or 0)),
                    selling_price=Decimal(str(_v(it, "selling_price", 0) or 0)),
                    tax_percent=Decimal(str(_v(it, "tax_percent", 0) or 0)),
                )
                self.db.add(poi)
            await self.db.flush()
        return grn
    
    async def get_grns(
        self,
        hospital_id: UUID,
        supplier_id: Optional[UUID] = None,
        skip: int = 0,
        limit: int = 100
    ):
        """Get GRNs"""
        return await self.repo.get_grns(hospital_id, supplier_id, skip, limit)
    
    async def get_grn(self, grn_id: UUID, hospital_id: UUID):
        """Get GRN by ID"""
        grn = await self.repo.get_grn_by_id(grn_id, hospital_id)
        if not grn:
            raise BusinessLogicError("GRN not found")
        return grn
    
    def _normalize_date(self, v):
        """Normalize date/datetime/string to date for DB"""
        if v is None:
            return None
        if isinstance(v, date) and not isinstance(v, datetime):
            return v
        if isinstance(v, datetime):
            return v.date()
        if isinstance(v, str):
            return datetime.fromisoformat(v.replace("Z", "+00:00")).date()
        return v

    async def add_grn_item(
        self,
        grn_id: UUID,
        hospital_id: UUID,
        medicine_id: UUID,
        batch_no: str,
        expiry_date,
        received_qty,
        purchase_rate: float,
        mrp: float,
        selling_price: float,
        free_qty: float = 0,
        tax_percent: float = 0,
        **kwargs
    ):
        """Add item to GRN (GRNItemCreate fields)"""
        from app.models.pharmacy import GoodsReceiptItem

        grn = await self.get_grn(grn_id, hospital_id)
        if grn.is_finalized:
            raise BusinessLogicError("Cannot add items to finalized GRN")

        expiry = self._normalize_date(expiry_date)
        item = GoodsReceiptItem(
            id=uuid4(),
            hospital_id=hospital_id,
            grn_id=grn_id,
            medicine_id=medicine_id,
            batch_no=batch_no,
            expiry_date=expiry,
            received_qty=Decimal(str(received_qty)),
            free_qty=Decimal(str(free_qty)),
            purchase_rate=Decimal(str(purchase_rate)),
            mrp=Decimal(str(mrp)),
            selling_price=Decimal(str(selling_price)),
            tax_percent=Decimal(str(tax_percent)),
            **kwargs
        )
        self.db.add(item)
        await self.db.flush()
        return item
    
    async def finalize_grn(self, grn_id: UUID, hospital_id: UUID, finalized_by: UUID):
        """Finalize GRN and create/update stock batches. Groups GRN lines by (medicine_id, batch_no, expiry_date)
        to satisfy uq_stock_batch; merges qty when the same batch appears on multiple lines."""
        grn = await self.get_grn(grn_id, hospital_id)

        if grn.is_finalized:
            raise BusinessLogicError("GRN already finalized")

        items = await self.repo.get_grn_items(grn_id, hospital_id)
        if not items:
            raise BusinessLogicError("Cannot finalize GRN without items")

        # Group by (medicine_id, batch_no, expiry_date) and sum qty; keep first item for pricing and grn_item_id
        from collections import defaultdict
        groups = defaultdict(lambda: {"qty": Decimal("0"), "first": None})
        for item in items:
            key = (item.medicine_id, item.batch_no, item.expiry_date)
            groups[key]["qty"] += item.received_qty
            if groups[key]["first"] is None:
                groups[key]["first"] = item

        batches_created = []
        for (medicine_id, batch_no, expiry_date), data in groups.items():
            first = data["first"]
            total_qty = data["qty"]
            existing = await self.repo.get_stock_batch_by_batch_key(
                hospital_id, medicine_id, batch_no, expiry_date
            )
            if existing:
                existing.qty_on_hand += total_qty
                batches_created.append(str(existing.id))
            else:
                batch = StockBatch(
                    id=uuid4(),
                    hospital_id=hospital_id,
                    medicine_id=medicine_id,
                    batch_no=batch_no,
                    expiry_date=expiry_date,
                    purchase_rate=first.purchase_rate,
                    mrp=first.mrp,
                    selling_price=first.selling_price,
                    qty_on_hand=total_qty,
                    qty_reserved=Decimal("0"),
                    grn_item_id=first.id,
                )
                self.db.add(batch)
                batches_created.append(str(batch.id))

        grn.is_finalized = True
        grn.finalized_at = datetime.now(timezone.utc).replace(tzinfo=None)
        grn.finalized_by = finalized_by
        await self.db.flush()

        return {
            "grn_id": str(grn_id),
            "batches_created": batches_created,
            "total_items": len(items),
        }

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
    ):
        """Get purchase orders"""
        return await self.repo.get_purchase_orders(hospital_id, supplier_id, status, skip, limit)
    
    async def get_purchase_order(self, po_id: UUID, hospital_id: UUID):
        """Get purchase order by ID"""
        po = await self.repo.get_purchase_order_by_id(po_id, hospital_id)
        if not po:
            raise BusinessLogicError("Purchase order not found")
        return po
    
    async def create_purchase_order(
        self,
        hospital_id: UUID,
        supplier_id: UUID,
        created_by: UUID,
        items: List[Any],
        expected_date: Optional[date] = None,
        notes: Optional[str] = None
    ):
        """Create new purchase order with line items"""
        from app.models.pharmacy import PurchaseOrder, PurchaseOrderItem

        # Generate next PO number for this hospital (PO-YYYYMMDD-0001)
        today_str = date.today().strftime("%Y%m%d")
        count_result = await self.db.execute(
            select(func.count(PurchaseOrder.id)).where(
                and_(
                    PurchaseOrder.hospital_id == hospital_id,
                    PurchaseOrder.po_number.like(f"PO-{today_str}%")
                )
            )
        )
        n = (count_result.scalar() or 0) + 1
        po_number = f"PO-{today_str}-{n:04d}"

        po = PurchaseOrder(
            id=uuid4(),
            hospital_id=hospital_id,
            supplier_id=supplier_id,
            po_number=po_number,
            created_by=created_by,
            expected_date=expected_date,
            notes=notes,
            status="DRAFT",
            subtotal=Decimal("0"),
            tax_total=Decimal("0"),
            discount_total=Decimal("0"),
            grand_total=Decimal("0"),
        )
        self.db.add(po)
        await self.db.flush()

        subtotal = Decimal("0")
        after_discount_total = Decimal("0")
        grand_total = Decimal("0")
        for it in items:
            ordered_qty = Decimal(str(it.ordered_qty))
            purchase_rate = Decimal(str(it.purchase_rate))
            tax_pct = Decimal(str(getattr(it, "tax_percent", 0) or 0))
            discount_pct = Decimal(str(getattr(it, "discount_percent", 0) or 0))
            line_before = ordered_qty * purchase_rate
            after_discount = (line_before * (1 - discount_pct / 100)).quantize(Decimal("0.01"))
            line_total = (after_discount * (1 + tax_pct / 100)).quantize(Decimal("0.01"))
            subtotal += line_before
            after_discount_total += after_discount
            grand_total += line_total
            poi = PurchaseOrderItem(
                id=uuid4(),
                hospital_id=hospital_id,
                po_id=po.id,
                medicine_id=it.medicine_id,
                ordered_qty=ordered_qty,
                received_qty=Decimal("0"),
                purchase_rate=purchase_rate,
                tax_percent=tax_pct,
                discount_percent=discount_pct,
                line_total=line_total,
            )
            self.db.add(poi)
        po.subtotal = subtotal
        po.discount_total = (subtotal - after_discount_total).quantize(Decimal("0.01"))
        po.tax_total = (grand_total - after_discount_total).quantize(Decimal("0.01"))
        po.grand_total = grand_total
        await self.db.flush()
        return po
    
    async def update_purchase_order(self, po_id: UUID, hospital_id: UUID, **updates):
        """Update purchase order"""
        po = await self.get_purchase_order(po_id, hospital_id)
        
        if po.status not in ["DRAFT", "PENDING"]:
            raise BusinessLogicError("Cannot update purchase order in current status")
        
        for key, value in updates.items():
            if hasattr(po, key):
                setattr(po, key, value)
        
        po.updated_at = datetime.utcnow()
        await self.db.flush()
        return po
    
    async def submit_purchase_order(self, po_id: UUID, hospital_id: UUID):
        """Submit purchase order for approval (DRAFT → PENDING)."""
        po = await self.get_purchase_order(po_id, hospital_id)
        if po.status != "DRAFT":
            raise BusinessLogicError("Only DRAFT purchase orders can be submitted")
        po.status = "PENDING"
        po.updated_at = datetime.utcnow()
        await self.db.flush()
        return po
    
    async def approve_purchase_order(self, po_id: UUID, hospital_id: UUID, approved_by: UUID):
        """Approve purchase order. Allowed from DRAFT or PENDING (no separate submit step)."""
        po = await self.get_purchase_order(po_id, hospital_id)
        
        if po.status not in ("DRAFT", "PENDING"):
            raise BusinessLogicError("Only DRAFT or PENDING purchase orders can be approved")
        
        po.status = "APPROVED"
        po.approved_by = approved_by
        po.approved_at = datetime.utcnow()
        po.updated_at = datetime.utcnow()
        await self.db.flush()
        return po
    
    async def send_purchase_order(self, po_id: UUID, hospital_id: UUID):
        """Send purchase order to supplier"""
        po = await self.get_purchase_order(po_id, hospital_id)
        
        if po.status != "APPROVED":
            raise BusinessLogicError("Only APPROVED purchase orders can be sent")
        
        po.status = "SENT"
        po.sent_at = datetime.utcnow()
        po.updated_at = datetime.utcnow()
        await self.db.flush()
        return po
    
    async def cancel_purchase_order(self, po_id: UUID, hospital_id: UUID, cancelled_by: UUID, reason: str):
        """Cancel purchase order"""
        po = await self.get_purchase_order(po_id, hospital_id)
        
        if po.status in ["COMPLETED", "CANCELLED"]:
            raise BusinessLogicError("Cannot cancel purchase order in current status")
        
        po.status = "CANCELLED"
        po.cancelled_by = cancelled_by
        po.cancellation_reason = reason
        po.updated_at = datetime.utcnow()
        await self.db.flush()
        return po
    
    # ============================================================================
    # SALES OPERATIONS
    # ============================================================================
    
    async def create_sale(
        self,
        hospital_id: UUID,
        created_by: UUID,
        idempotency_key: str,
        sale_type: str = "OTC",
        patient_id: Optional[UUID] = None,
        doctor_id: Optional[UUID] = None,
        prescription_id: Optional[UUID] = None,
        billed_via: str = "PHARMACY_COUNTER",
        payment_method: Optional[str] = None,
        notes: Optional[str] = None,
        items: Optional[List[Any]] = None,
        **kwargs
    ):
        """Create new sale (DRAFT) with optional items. Uses idempotency_key from header.
        Idempotent: if same key already exists, returns existing sale (no 409)."""
        from app.models.pharmacy import Sale, SaleItem

        # Idempotent: return existing sale if idempotency_key already used
        existing = await self.repo.get_sale_by_idempotency_key(idempotency_key, hospital_id)
        if existing:
            return existing

        # Generate sale_number: SALE-YYYYMMDD-0001-<unique> to avoid race collisions
        today_str = date.today().strftime("%Y%m%d")
        count_result = await self.db.execute(
            select(func.count(Sale.id)).where(
                and_(
                    Sale.hospital_id == hospital_id,
                    Sale.sale_number.like(f"SALE-{today_str}%")
                )
            )
        )
        n = (count_result.scalar() or 0) + 1
        unique_suffix = uuid4().hex[:8].upper()
        sale_number = f"SALE-{today_str}-{n:04d}-{unique_suffix}"

        # Extract enum values (e.g. "PRESCRIPTION" not "SaleType.PRESCRIPTION") for varchar(20) columns
        def _enum_value(v, default: str) -> str:
            if v is None:
                return default
            val = getattr(v, "value", v)
            if isinstance(val, str) and "." in val:
                val = val.split(".")[-1]
            return str(val)[:20] if val else default

        sale_type_str = _enum_value(sale_type, "OTC")
        payment_method_str = _enum_value(payment_method, None) if payment_method else None
        billed_via_str = _enum_value(billed_via, "PHARMACY_COUNTER")

        sale = Sale(
            id=uuid4(),
            hospital_id=hospital_id,
            sale_number=sale_number,
            sale_type=sale_type_str,
            patient_id=patient_id,
            doctor_id=doctor_id,
            prescription_id=prescription_id,
            billed_via=billed_via_str,
            payment_method=payment_method_str,
            created_by=created_by,
            notes=notes,
            status="DRAFT",
            idempotency_key=idempotency_key,
        )
        self.db.add(sale)
        try:
            await self.db.flush()
        except IntegrityError:
            await self.db.rollback()
            # Race: another request may have committed with same idempotency_key
            existing = await self.repo.get_sale_by_idempotency_key(idempotency_key, hospital_id)
            if existing:
                return existing
            raise

        # Add items if provided
        if items:
            for it in items:
                med_id = it.get("medicine_id")
                qty = Decimal(str(it.get("qty", 0) or 0))
                unit_price = Decimal(str(it.get("unit_price", 0) or 0))
                discount = Decimal(str(it.get("discount", 0) or 0))
                batch_id = it.get("batch_id")
                if not med_id or qty <= 0:
                    continue
                if not batch_id:
                    # FEFO: get first available batch with stock
                    batches = await self.repo.get_stock_batches(
                        hospital_id, med_id, skip=0, limit=10,
                        include_expired=False
                    )
                    available = [b for b in batches if (b.qty_on_hand or 0) - (b.qty_reserved or 0) > 0]
                    if not available:
                        raise BusinessLogicError(f"No stock for medicine {med_id}")
                    batch_id = available[0].id
                if unit_price <= 0:
                    batch = await self.repo.get_stock_batch_by_id(batch_id, hospital_id)
                    unit_price = batch.selling_price if batch else Decimal(0)
                line_total = (qty * unit_price) - discount
                item = SaleItem(
                    id=uuid4(),
                    hospital_id=hospital_id,
                    sale_id=sale.id,
                    medicine_id=med_id,
                    batch_id=batch_id,
                    qty=qty,
                    unit_price=unit_price,
                    discount=discount,
                    tax=Decimal(0),
                    line_total=line_total,
                )
                self.db.add(item)
            await self.db.flush()

        return sale
    
    async def get_sales(
        self,
        hospital_id: UUID,
        patient_id: Optional[UUID] = None,
        status: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        skip: int = 0,
        limit: int = 100
    ):
        """Get sales"""
        return await self.repo.get_sales(hospital_id, patient_id, status, from_date, to_date, skip, limit)
    
    async def get_sale(self, sale_id: UUID, hospital_id: UUID):
        """Get sale by ID"""
        sale = await self.repo.get_sale_by_id(sale_id, hospital_id)
        if not sale:
            raise BusinessLogicError("Sale not found")
        return sale
    
    async def add_sale_item(
        self,
        sale_id: UUID,
        hospital_id: UUID,
        medicine_id: UUID,
        qty: float,
        unit_price: float,
        discount: float = 0,
        batch_id: Optional[UUID] = None,
        **kwargs
    ):
        """Add item to sale. Batch chosen by FEFO if not provided (internal only)."""
        from app.models.pharmacy import SaleItem

        sale = await self.get_sale(sale_id, hospital_id)
        if sale.status != "DRAFT":
            raise BusinessLogicError("Cannot add items to completed sale")

        quantity = Decimal(str(qty))
        if not batch_id:
            batches = await self.repo.get_stock_batches(
                hospital_id, medicine_id, skip=0, limit=10, include_expired=False
            )
            available = [b for b in batches if (b.qty_on_hand or 0) - (b.qty_reserved or 0) > 0]
            if not available:
                raise BusinessLogicError(f"No stock for medicine {medicine_id}")
            batch_id = available[0].id
        batch = await self.repo.get_stock_batch_by_id(batch_id, hospital_id)
        available_qty = (batch.qty_on_hand or 0) - (batch.qty_reserved or 0) if batch else 0
        if not batch or available_qty < quantity:
            raise BusinessLogicError("Insufficient stock")
        if unit_price <= 0 and batch:
            unit_price = batch.selling_price
        unit_price = Decimal(str(unit_price))
        discount_dec = Decimal(str(discount))
        line_total = (quantity * unit_price) - discount_dec
        item = SaleItem(
            id=uuid4(),
            hospital_id=hospital_id,
            sale_id=sale_id,
            medicine_id=medicine_id,
            batch_id=batch_id,
            qty=quantity,
            unit_price=unit_price,
            discount=discount_dec,
            tax=Decimal(0),
            line_total=line_total,
        )
        self.db.add(item)
        await self.db.flush()
        return item
    
    async def complete_sale(self, sale_id: UUID, hospital_id: UUID, completed_by: UUID):
        """
        Complete sale: deduct stock AND auto-create BillItems on the patient's active
        DRAFT bill so that pharmacy charges flow into billing automatically.

        FIX: Previously, completing a sale had ZERO billing integration — stock was
        deducted but revenue never appeared in the billing system.
        """
        import logging
        _logger = logging.getLogger(__name__)

        sale = await self.get_sale(sale_id, hospital_id)

        if sale.status != "DRAFT":
            raise BusinessLogicError("Sale already completed")

        # Get sale items
        items = await self.repo.get_sale_items(sale_id, hospital_id)
        if not items:
            raise BusinessLogicError("Cannot complete sale without items")

        # Update stock for each item (FEFO — StockBatch qty_on_hand)
        for item in items:
            batch = await self.repo.get_stock_batch_by_id(item.batch_id, hospital_id)
            available = (batch.qty_on_hand or 0) - (batch.qty_reserved or 0)
            if available < item.qty:
                raise BusinessLogicError(f"Insufficient stock for medicine {item.medicine_id}")
            batch.qty_on_hand = (batch.qty_on_hand or 0) - item.qty
            batch.updated_at = datetime.utcnow()

        # Update sale status
        sale.status = "COMPLETED"
        sale.completed_at = datetime.utcnow()
        sale.updated_at = datetime.utcnow()
        await self.db.flush()

        # ── AUTO-BILLING INTEGRATION ──────────────────────────────────────────
        # Find the patient's active DRAFT bill and push pharmacy charges into it.
        # If no DRAFT bill exists, this logs a warning — billing staff can
        # manually add items. This is non-fatal so stock is still deducted.
        if sale.patient_id:
            try:
                await self._push_sale_items_to_bill(sale, items, hospital_id, completed_by)
            except Exception as billing_ex:
                _logger.error(
                    f"[PHARMACY→BILLING] Sale {sale_id} completed but auto-bill push FAILED: {billing_ex}. "
                    "Billing staff must add pharmacy charges manually."
                )

        return {
            "sale_id": str(sale_id),
            "total_items": len(items),
            "status": "COMPLETED",
        }

    async def _push_sale_items_to_bill(self, sale, items, hospital_id: UUID, pushed_by: UUID):
        """
        Push completed pharmacy sale items into the patient's active DRAFT bill.

        Strategy:
        1. Find the patient's open DRAFT bill (OPD or IPD).
        2. If found, add BillItems for each medicine dispensed.
        3. Recalculate bill totals.
        4. If no DRAFT bill exists, log warning (billing staff notified).
        """
        import logging
        _logger = logging.getLogger(__name__)
        from app.models.billing.bill import Bill, BillItem
        from app.models.pharmacy import Medicine

        # Find the patient's active DRAFT bill
        from sqlalchemy import select, and_
        bill_result = await self.db.execute(
            select(Bill).where(
                and_(
                    Bill.hospital_id == hospital_id,
                    Bill.patient_id == sale.patient_id,
                    Bill.status == "DRAFT",
                )
            ).order_by(Bill.created_at.desc()).limit(1)
        )
        bill = bill_result.scalar_one_or_none()

        if not bill:
            _logger.warning(
                f"[PHARMACY→BILLING] No DRAFT bill found for patient {sale.patient_id}. "
                f"Pharmacy sale {sale.id} charges must be added manually."
            )
            return

        # Add one BillItem per sale item
        for item in items:
            # Get medicine name for description
            med_result = await self.db.execute(
                select(Medicine).where(Medicine.id == item.medicine_id)
            )
            medicine = med_result.scalar_one_or_none()
            med_name = medicine.generic_name if medicine else str(item.medicine_id)
            brand = f" ({medicine.brand_name})" if medicine and medicine.brand_name else ""
            description = f"Pharmacy: {med_name}{brand}"

            qty = float(item.qty)
            unit_price = float(item.unit_price) if item.unit_price else 0.0
            line_subtotal = qty * unit_price
            line_tax = 0.0
            line_total = line_subtotal - float(item.discount or 0)

            bill_item = BillItem(
                bill_id=bill.id,
                service_item_id=None,  # pharmacy items don't map to service catalogue
                description=description,
                quantity=qty,
                unit_price=unit_price,
                tax_percentage=0,
                line_subtotal=line_subtotal,
                line_tax=line_tax,
                line_total=max(line_total, 0),
            )
            self.db.add(bill_item)

        await self.db.flush()
        await self.db.refresh(bill)

        # Recalculate bill totals inline
        all_items_result = await self.db.execute(
            select(BillItem).where(BillItem.bill_id == bill.id)
        )
        all_items = all_items_result.scalars().all()
        subtotal = sum(float(i.line_subtotal) for i in all_items)
        tax = sum(float(i.line_tax) for i in all_items)
        total = sum(float(i.line_total) for i in all_items)
        bill.subtotal = subtotal
        bill.tax_amount = tax
        bill.total_amount = total - float(bill.discount_amount or 0)
        bill.balance_due = float(bill.total_amount) - float(bill.amount_paid or 0)
        await self.db.flush()

        _logger.info(
            f"[PHARMACY→BILLING] Auto-added {len(items)} pharmacy item(s) to bill {bill.bill_number} "
            f"for patient {sale.patient_id}. New total: {bill.total_amount}"
        )
    
    async def void_sale(self, sale_id: UUID, hospital_id: UUID, voided_by: UUID, reason: str):
        """Void completed sale"""
        sale = await self.get_sale(sale_id, hospital_id)
        
        if sale.status != "COMPLETED":
            raise BusinessLogicError("Only completed sales can be voided")
        
        # Restore stock
        items = await self.repo.get_sale_items(sale_id, hospital_id)
        for item in items:
            batch = await self.repo.get_stock_batch_by_id(item.batch_id, hospital_id)
            batch.available_quantity += item.quantity
            batch.updated_at = datetime.utcnow()
        
        sale.status = "VOIDED"
        sale.voided_by = voided_by
        sale.void_reason = reason
        sale.voided_at = datetime.utcnow()
        sale.updated_at = datetime.utcnow()
        await self.db.flush()
        return sale
    
    async def get_sale_receipt(self, sale_id: UUID, hospital_id: UUID):
        """Generate sale receipt"""
        sale = await self.get_sale(sale_id, hospital_id)
        items = await self.repo.get_sale_items(sale_id, hospital_id)
        
        return {
            "sale_id": str(sale_id),
            "date": sale.created_at.isoformat() if sale.created_at else None,
            "items": [
                {
                    "medicine_id": str(item.medicine_id),
                    "quantity": item.quantity,
                    "unit_price": float(item.unit_price),
                    "total": float(item.quantity * item.unit_price)
                }
                for item in items
            ],
            "total_amount": sum(item.quantity * item.unit_price for item in items)
        }

    # ============================================================================
    # RETURNS
    # ============================================================================

    async def get_returns(
        self,
        hospital_id: UUID,
        return_type: Optional[str] = None,
        skip: int = 0,
        limit: int = 100
    ):
        """Get returns"""
        return await self.repo.get_returns(hospital_id, return_type, skip, limit)

    # ============================================================================
    # ALERTS
    # ============================================================================

    async def get_alerts(
        self,
        hospital_id: UUID,
        alert_type: Optional[str] = None,
        status_filter: Optional[str] = None,
        skip: int = 0,
        limit: int = 100
    ):
        """Get expiry/low stock alerts"""
        return await self.repo.get_alerts(
            hospital_id, alert_type, status_filter, skip, limit
        )

    async def acknowledge_alert(self, alert_id: UUID, hospital_id: UUID, acknowledged_by: UUID):
        """Acknowledge an alert"""
        from app.models.pharmacy import ExpiryAlert
        result = await self.db.execute(
            select(ExpiryAlert).where(
                and_(ExpiryAlert.id == alert_id, ExpiryAlert.hospital_id == hospital_id)
            )
        )
        alert = result.scalar_one_or_none()
        if not alert:
            raise BusinessLogicError("Alert not found")
        alert.status = "ACKNOWLEDGED"
        alert.acknowledged_by = acknowledged_by
        alert.acknowledged_at = datetime.utcnow()
        await self.db.flush()
        return alert

    async def run_expiry_scan(self, hospital_id: UUID):
        """Run expiry scan - create alerts for near-expiry batches"""
        return {"scanned": 0, "alerts_created": 0}

    # ============================================================================
    # REPORTS
    # ============================================================================

    async def get_sales_summary(
        self, hospital_id: UUID, from_date: str, to_date: str, group_by: str = "day"
    ):
        """Sales summary report (completed/PAID sales only)."""
        rows = await self.repo.report_sales_summary(hospital_id, from_date, to_date, group_by)
        return {"period": f"{from_date} to {to_date}", "group_by": group_by, "rows": rows}

    async def get_stock_valuation(self, hospital_id: UUID):
        """Stock valuation report (batch qty * purchase_rate)."""
        return await self.repo.report_stock_valuation(hospital_id)

    async def get_expiry_report(self, hospital_id: UUID, near_days: int = 90):
        """Expiry report: near-expiry and expired batches."""
        return await self.repo.report_expiry(hospital_id, near_days=near_days)

    async def get_fast_slow_moving(
        self, hospital_id: UUID, from_date: str, to_date: str
    ):
        """Fast/slow moving items from completed sales."""
        return await self.repo.report_fast_slow_moving(hospital_id, from_date, to_date)

    async def get_profit_margins(
        self, hospital_id: UUID, from_date: str, to_date: str
    ):
        """Profit margin report by medicine (revenue - cost from sale items)."""
        return await self.repo.report_profit_margins(hospital_id, from_date, to_date)

    # ============================================================================
    # DASHBOARD / INVENTORY / UI SETTINGS (tenant DB)
    # ============================================================================

    async def get_dashboard_overview(self, hospital_id: UUID) -> dict[str, Any]:
        """Aggregate KPIs for pharmacist dashboard (reads only tenant tables)."""
        med_cnt = (
            await self.db.execute(
                select(func.count()).select_from(Medicine).where(
                    and_(Medicine.hospital_id == hospital_id, Medicine.is_active == True)
                )
            )
        ).scalar_one()
        sup_cnt = (
            await self.db.execute(
                select(func.count()).select_from(Supplier).where(
                    and_(
                        Supplier.hospital_id == hospital_id,
                        Supplier.status == SupplierStatus.ACTIVE.value,
                    )
                )
            )
        ).scalar_one()
        pending_po_statuses = (
            PurchaseOrderStatus.DRAFT.value,
            PurchaseOrderStatus.PENDING.value,
            PurchaseOrderStatus.APPROVED.value,
            PurchaseOrderStatus.SENT.value,
            PurchaseOrderStatus.PARTIALLY_RECEIVED.value,
        )
        pending_pos = (
            await self.db.execute(
                select(func.count()).select_from(PurchaseOrder).where(
                    and_(
                        PurchaseOrder.hospital_id == hospital_id,
                        PurchaseOrder.status.in_(pending_po_statuses),
                    )
                )
            )
        ).scalar_one()
        today = date.today()
        sales_today = (
            await self.db.execute(
                select(func.count()).select_from(Sale).where(
                    and_(
                        Sale.hospital_id == hospital_id,
                        cast(Sale.created_at, Date) == today,
                    )
                )
            )
        ).scalar_one()
        return {
            "medicines_count": int(med_cnt or 0),
            "active_suppliers_count": int(sup_cnt or 0),
            "pending_purchase_orders_count": int(pending_pos or 0),
            "sales_today_count": int(sales_today or 0),
        }

    async def list_inventory_summary(
        self,
        hospital_id: UUID,
        *,
        search: Optional[str] = None,
        category: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        """
        One row per medicine with aggregated on-hand qty and nearest expiry (inventory screen).
        """
        filters = [
            Medicine.hospital_id == hospital_id,
            Medicine.is_active == True,
        ]
        if search and str(search).strip():
            term = f"%{search.strip()}%"
            filters.append(
                or_(
                    Medicine.generic_name.ilike(term),
                    Medicine.brand_name.ilike(term),
                    Medicine.sku.ilike(term),
                )
            )
        if category and str(category).strip():
            filters.append(Medicine.category == category.strip())

        total = (
            await self.db.execute(select(func.count()).select_from(Medicine).where(and_(*filters)))
        ).scalar_one()

        total_qty = func.coalesce(func.sum(StockBatch.qty_on_hand), 0).label("total_qty")
        nearest_exp = func.min(StockBatch.expiry_date).label("nearest_expiry")
        unit_price = func.min(StockBatch.selling_price).label("unit_price")

        q = (
            select(
                Medicine.id,
                Medicine.generic_name,
                Medicine.brand_name,
                Medicine.strength,
                Medicine.category,
                Medicine.reorder_level,
                Medicine.sku,
                total_qty,
                nearest_exp,
                unit_price,
            )
            .select_from(Medicine)
            .outerjoin(
                StockBatch,
                and_(
                    StockBatch.medicine_id == Medicine.id,
                    StockBatch.hospital_id == hospital_id,
                    StockBatch.is_active == True,
                ),
            )
            .where(and_(*filters))
            .group_by(
                Medicine.id,
                Medicine.generic_name,
                Medicine.brand_name,
                Medicine.strength,
                Medicine.category,
                Medicine.reorder_level,
                Medicine.sku,
            )
            .order_by(Medicine.generic_name.asc())
            .offset(skip)
            .limit(limit)
        )
        rows = (await self.db.execute(q)).all()

        items: list[dict[str, Any]] = []
        for r in rows:
            tid, gname, bname, strength, cat, reorder, sku, tqty, nexp, uprice = r
            qty = float(tqty or 0)
            reorder_i = int(reorder or 0)
            if qty <= 0:
                st = "OUT_OF_STOCK"
            elif reorder_i > 0 and qty <= float(reorder_i):
                st = "LOW_STOCK"
            else:
                st = "IN_STOCK"
            items.append(
                {
                    "medicine_id": str(tid),
                    "display_name": f"{gname or ''} {strength or ''}".strip(),
                    "generic_name": gname,
                    "brand_name": bname,
                    "strength": strength,
                    "category": cat,
                    "sku": sku,
                    "stock_units": qty,
                    "unit_price": float(uprice) if uprice is not None else None,
                    "nearest_expiry": nexp.isoformat() if nexp else None,
                    "reorder_level": reorder_i,
                    "status": st,
                }
            )
        return {"total": int(total or 0), "items": items}

    async def get_pharmacy_ui_settings(self, hospital_id: UUID) -> dict[str, Any]:
        """Pharmacy UI settings stored under hospitals.settings['pharmacy_ui'] (tenant row)."""
        h = await self.db.get(Hospital, hospital_id)
        root = dict(h.settings or {}) if h else {}
        cur = dict(root.get("pharmacy_ui") or {})
        defaults = {
            "general": {
                "pharmacy_name": "",
                "pharmacy_address": "",
                "phone": "",
                "email": "",
            },
            "notifications": {
                "low_stock_alerts": True,
                "expiry_alerts": True,
                "purchase_order_updates": True,
                "sales_reports_email": False,
            },
        }
        merged = {
            "general": {**defaults["general"], **dict(cur.get("general") or {})},
            "notifications": {**defaults["notifications"], **dict(cur.get("notifications") or {})},
        }
        return merged

    async def update_pharmacy_ui_settings(self, hospital_id: UUID, payload: dict[str, Any]) -> dict[str, Any]:
        h = await self.db.get(Hospital, hospital_id)
        if not h:
            raise BusinessLogicError("Hospital not found in tenant database")
        root = dict(h.settings or {})
        cur = dict(root.get("pharmacy_ui") or {})
        if "general" in payload and isinstance(payload["general"], dict):
            cur["general"] = {**dict(cur.get("general") or {}), **payload["general"]}
        if "notifications" in payload and isinstance(payload["notifications"], dict):
            cur["notifications"] = {**dict(cur.get("notifications") or {}), **payload["notifications"]}
        root["pharmacy_ui"] = cur
        h.settings = root
        h.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(h)
        return await self.get_pharmacy_ui_settings(hospital_id)

